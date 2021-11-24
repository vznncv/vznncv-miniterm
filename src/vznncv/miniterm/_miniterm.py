"""
Line buffered version of serial.tools.miniterm.
"""
import asyncio
import logging
import os.path
import shutil
import subprocess
import sys
from typing import Optional

import prompt_toolkit
import prompt_toolkit.lexers
import prompt_toolkit.shortcuts
import serial.tools.list_ports
import serial_asyncio
from prompt_toolkit.application import in_terminal
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from serial.tools.list_ports_common import ListPortInfo

logger = logging.getLogger("vznncv-miniterm")


##
# Helper prompt_toolkit wrapper
##


class InteractiveShell(prompt_toolkit.PromptSession):
    def __init__(self, *, prompt_color, input_color, async_output_color, sync_output_color, **kwargs):
        self._prompt_color = prompt_color
        self._input_color = input_color
        self._async_output_color = async_output_color
        self._sync_output_color = sync_output_color
        self._async_output_buffer = []
        self.last_exc = None

        message = kwargs.get('message')
        if isinstance(message, str) and self._prompt_color is not None:
            kwargs['message'] = FormattedText([(self._prompt_color, message)])

        lexer = kwargs.get('lexer')
        if lexer is None and self._input_color is not None:
            kwargs['lexer'] = prompt_toolkit.lexers.SimpleLexer(self._input_color)

        # prepare key bindings
        key_bindings = KeyBindings()

        @key_bindings.add('c-r')
        def reset_screen(event):
            output_name = self.app.output.__class__.__name__.lower()
            if 'vt100' in output_name:
                # run "full" cleanup for vt100
                output = self.app.output
                output.write_raw("\x1bc")
                output.flush()
                self.app.reset()
            prompt_toolkit.shortcuts.clear()

        super().__init__(key_bindings=key_bindings, **kwargs)

        # HACK: hard code number of rows bellow cursor
        #       to eliminate CPR request
        def get_rows_below_cursor_position():
            return 1

        self.app.output.get_rows_below_cursor_position = get_rows_below_cursor_position

    def _cleanup_text(self, text):
        return text.replace('\r', '')

    def write_sync(self, text):
        text = self._cleanup_text(text)
        prompt_toolkit.print_formatted_text(FormattedText([(self._sync_output_color, text)]), end='')

    async def write_line_async(self, text):
        text = self._cleanup_text(text)
        text = text.strip()
        async with in_terminal():
            prompt_toolkit.print_formatted_text(FormattedText([(self._async_output_color, text)]), end='\n')


##
# Serial port utils
##

class Transform(object):
    """do-nothing: forward all data unchanged"""

    def rx(self, text):
        """text received from serial port"""
        return text

    def tx(self, text):
        """text to be sent to serial port"""
        return text


class CRLF(Transform):
    """ENTER sends CR+LF"""

    def rx(self, text):
        return text.replace('\r', '')

    def tx(self, text):
        return text.replace('\n', '\r\n')


class CR(Transform):
    """ENTER sends CR"""

    def rx(self, text):
        return text.replace('\r', '\n')

    def tx(self, text):
        return text.replace('\n', '\r')


class LF(Transform):
    """ENTER sends LF"""


##
# cli entry point
##


def parse_transform(value):
    value = value.lower()
    if value == 'cr':
        return CR
    elif value == 'lf':
        return LF
    elif value == 'crlf':
        return CRLF
    else:
        raise ValueError(f"Invalid EOL transformation: {value}")


def _canonic_port_form(port):
    if sys.platform.startswith('linux'):
        return os.path.realpath(port)
    else:
        return None


def _check_device(port: str) -> ListPortInfo:
    serial_ports = list(serial.tools.list_ports.comports())

    for serial_port in serial_ports:
        if serial_port.device == port:
            return serial_port
    canonic_port = _canonic_port_form(port)
    if canonic_port is not None:
        for serial_port in serial_ports:
            if serial_port.device == canonic_port:
                return serial_port

    raise ValueError(f"{port} isn't a serial port")


class _SerialOutput(asyncio.Protocol):
    def __init__(self, ps: InteractiveShell, transform: Transform, loop: asyncio.AbstractEventLoop,
                 output_file: Optional[str] = None):
        super().__init__()
        self._ps: InteractiveShell = ps
        self._transform: Transform = transform
        self._loop = loop
        self._transport: Optional[serial_asyncio.SerialTransport] = None
        self._buf = []
        self._newline_sep = transform.tx('\n').encode('utf-8')[0:1]
        self._output_file_path = os.path.abspath(output_file) if output_file is not None else None
        self._output_file_obj = None

    def connection_made(self, transport: serial_asyncio.SerialTransport):
        self._transport = transport
        self._buf.clear()
        if self._output_file_path is not None and self._output_file_obj is None:
            self._output_file_obj = open(self._output_file_path, 'w', encoding='utf-8')

            async def _sync_output_file():
                while self._output_file_obj is not None:
                    await asyncio.sleep(2)
                    self._output_file_obj.flush()

            asyncio.ensure_future(_sync_output_file(), loop=self._loop)

    def _consume_data(self, data):
        data_blocks = data.split(self._newline_sep)
        self._buf.append(data_blocks[0])
        for next_block in data_blocks[1:]:
            raw_line = b''.join(self._buf)
            self._buf.clear()
            self._buf.append(next_block)

            line = raw_line.decode(encoding='utf-8', errors="ignore")
            line = self._transform.rx(line)

            if self._output_file_obj is not None:
                self._output_file_obj.write(line)

            asyncio.ensure_future(self._ps.write_line_async(line), loop=self._loop)

    def data_received(self, data):
        self._consume_data(data)

    def connection_lost(self, exc):
        self._consume_data(self._newline_sep)
        if self._output_file_obj is not None:
            self._output_file_obj.close()
            self._output_file_obj = None

        self._transport.loop.stop()
        self._transport = None
        self._buf.clear()
        self._ps.last_exc = exc
        raise exc


async def _process_input_async(ps: InteractiveShell, transport: serial_asyncio.SerialTransport, transform: Transform):
    newline_sym = transform.tx('\n')
    while True:
        tx_data = await ps.prompt_async()
        tx_data = tx_data + newline_sym
        tx_data = tx_data.encode('utf-8')
        transport.write(tx_data)


async def _async_serial_console(ps: InteractiveShell, port: str, baudrate: int, transform: Transform,
                                output_file: Optional[str] = None):
    loop = asyncio.get_event_loop()
    transport, protocol = await serial_asyncio.create_serial_connection(
        loop=loop,
        protocol_factory=lambda: _SerialOutput(ps=ps, transform=transform, loop=loop, output_file=output_file),
        url=port,
        baudrate=baudrate
    )
    asyncio.ensure_future(_process_input_async(ps, transport, transform))


def _clear_tty_settings():
    stty_path = shutil.which('stty')
    if stty_path is not None:
        subprocess.check_call([stty_path, 'sane'])


##
# CLI
##

def miniterm(device: str, baudrate: int, eol='lf', output_file: Optional[str] = None):
    """
    Simple interactive line-buffered terminal.

    :param device: device
    :param baudrate: baudrate
    :param eol: end of line symbol
    :param output_file: optional file to save console output
    :return:
    """
    # resolve device description if it's available
    for device_info in serial.tools.list_ports.comports():
        if device_info.device == device:
            device_description = device_info.description
            break
    else:
        device_description = 'n/a'

    # resolve transform
    eol = eol.lower()
    transform = parse_transform(eol)()

    # run console
    ps = InteractiveShell(
        message='> ',
        sync_output_color='#7542f5',
        async_output_color='#ED7621',
        prompt_color='#00ff66',
        input_color='#1642C7'
    )
    try:
        ps.write_sync(f'Connect to serial port.\n')
        ps.write_sync(f'- device: {device} ({device_description})\n')
        ps.write_sync(f'- baudrate: {baudrate}\n')
        ps.write_sync(f'- eol: {eol}\n')
        if output_file is not None:
            ps.write_sync(f'- output file: {output_file}\n')
        ps.write_sync(f'Use Ctrl+R to reset screen\n')
        ps.write_sync(f'Use Ctrl+C to exit\n')
        loop = asyncio.get_event_loop()

        # configure exception handler to suppress KeyboardInterrupt messages
        def suppress_keyboard_interrupt_handler(loop, context):
            if isinstance(context['exception'], KeyboardInterrupt):
                return
            loop.default_exception_handler(context)

        loop.set_exception_handler(suppress_keyboard_interrupt_handler)

        loop.run_until_complete(_async_serial_console(
            ps=ps,
            port=device,
            baudrate=baudrate,
            transform=transform,
            output_file=output_file
        ))
        loop.run_forever()
    except KeyboardInterrupt:
        ps.write_sync('Stop ...\n')
    finally:
        # restore tty settings
        _clear_tty_settings()

        if ps.last_exc is not None:
            print(f"ERROR: {ps.last_exc}", file=sys.stderr)
            return 1

    return 0
