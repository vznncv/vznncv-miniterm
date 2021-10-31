import argparse
import json
import logging
import re
import sys
import time
from functools import partial
from typing import Union, List

import serial.tools.list_ports

from ._miniterm import miniterm as _miniterm_entrypoint


class SerialPortSearcher:
    """
    Helper class to search serial port with specified filters.
    """
    _CACHED_PORT_TIME = 0.5
    _cached_ports = (None, None)

    @classmethod
    def list_comports(cls):

        # check cached results
        last_timestamp = cls._cached_ports[0]
        if last_timestamp is not None and last_timestamp + cls._CACHED_PORT_TIME > time.time():
            return cls._cached_ports[1]

        # scan serial ports
        serial_ports = list(serial.tools.list_ports.comports())
        # clear unknown ports
        serial_ports = [port for port in serial_ports if port.location]
        # add interface number to port data
        for port in serial_ports:
            m = re.search(r':.*\.(\d+)$', port.location)
            if m is None:
                port.interface_number = None
            else:
                port.interface_number = int(m.group(1))

        cls._cached_ports = (time.time(), serial_ports)
        return serial_ports

    _FILED_FILTERS = {
        'vid': dict(
            type=partial(int, base=16),
            description="vendor id",
            port_info_field='vid'
        ),
        'pid': dict(
            type=partial(int, base=16),
            description="product id",
            port_info_field='pid'
        ),
        'serial_number': dict(
            type=str,
            description="unique serial port number (note: it may absent)",
            port_info_field='serial_number'
        ),
        'ifno': dict(
            type=int,
            description="usb device interface number if it contains multiple interfaces",
            port_info_field='interface_number'
        ),
        'port': dict(
            type=str,
            description="explicit serial device path",
            port_info_field='device'
        )
    }

    @classmethod
    def _format_usb_id(cls, vid, pid):
        if vid is None or pid is None:
            return 'n/a'
        else:
            return f'{vid:04X}:{pid:04X}'

    @classmethod
    def port_description(cls, port):
        return f'{port.device}; {port.description}; ' \
               f'{port.hwid}{f" (ifno = {port.interface_number})" if port.interface_number is not None else ""}'

    @classmethod
    def format_comports(cls, ports=None):
        """
        Create message with serial ports
        """
        if ports is None:
            ports = cls.list_comports()
        return '\n'.join(f'- {cls.port_description(port)}' for port in ports)

    @classmethod
    def format_filter_help(cls):
        """
        Create filter description.
        """
        help_lines = []

        help_lines.append(
            'Filter represent json object {"k1": "v1", "k2": "v2", ... } or string k1=v1&&k2=v2&&... with '
            'the following fields:')
        for field_name, field_definition in cls._FILED_FILTERS.items():
            help_lines.append(f'- {field_name} - {field_definition["description"]};')
        help_lines.append('Filter can be specified multiple times to match device against any one.')
        return '\n'.join(help_lines)

    _KV_FILTER_RE = re.compile(r'^(?P<name>[\w]+)={1,2}(?P<value>[\w-]+)$')

    @classmethod
    def _load_filter_info_from_str(cls, filter_info: str) -> dict:
        try:
            # try to parse filter as json string
            return json.loads(filter_info)
        except Exception:
            pass
        # try to parse string as k1=v1&&k2=v2&&k3=v3 string
        result = {}
        for field_filter in filter_info.strip().split("&&"):
            m = cls._KV_FILTER_RE.match(field_filter)
            if m is None:
                raise ValueError(f"Invalid filter expression \"{field_filter}\" in \"{filter_info}\"")
            result[m.group('name')] = m.group('value')
        return result

    @classmethod
    def _parse_scalar_filter(cls, filter_info: Union[None, str, dict]) -> dict:
        if filter_info is None:
            return {}
        elif isinstance(filter_info, str):
            return cls._load_filter_info_from_str(filter_info)
        elif isinstance(filter_info, dict):
            return filter_info
        else:
            raise ValueError(f"Invalid filter: {filter_info}")

    def __init__(self, filter_info: Union[None, str, dict, List[Union[dict, str]]], no_input: bool = True):
        if filter_info is None or isinstance(filter, (str, dict)):
            filters_info = [self._parse_scalar_filter(filter_info)]
        elif isinstance(filter_info, list):
            filters_info = [self._parse_scalar_filter(item) for item in filter_info]
        else:
            raise ValueError(f"Invalid filter: {filter_info}")

        self._raw_filter_info = filters_info
        self._filters_info = []
        self._no_input = no_input
        # check filter definition
        for filter_info in filters_info:
            result_filter_info = {}
            self._filters_info.append(result_filter_info)
            for k, v in filter_info.items():
                k = k.lower()
                if k not in self._FILED_FILTERS:
                    raise ValueError(f"Unknown filter field \"{k}\"")
                try:
                    result_filter_info[k] = self._FILED_FILTERS[k]['type'](v)
                except Exception as e:
                    raise ValueError(f"Invalid value \"{v}\" of field \"{k}\"") from e

    def _format_filter_info(self):
        filter_components = [
            ' and '.join(f'{k} == {v}' for k, v in filter_info.items())
            for filter_info in self._filters_info
        ]
        filter_str = ' or '.join(map('({})'.format, filter_components))
        return f"serial filters {filter_str}"

    class ResolveError(ValueError):
        pass

    def _filter_impl(self, ports):
        matched_ports = []
        for port in ports:

            match_flag = False
            for filter_info in self._filters_info:
                for field_name, expected_value in filter_info.items():
                    filter_definition = self._FILED_FILTERS[field_name]
                    port_value = getattr(port, filter_definition['port_info_field'])
                    if expected_value != port_value:
                        break
                else:
                    match_flag = True
                if match_flag:
                    break
            if match_flag:
                matched_ports.append(port)

        return matched_ports

    def filter(self):
        """
        Return serial ports that are matched to a filter
        """
        ports = self.list_comports()
        return self._filter_impl(ports)

    def list_filtered_ports(self):
        return self._filter_impl(self.list_comports())

    def resolve(self):
        """
        Resolve serial port.
        """
        ports = self.list_comports()
        matched_ports = self._filter_impl(ports)

        if len(matched_ports) == 0:
            raise self.ResolveError(f"No ports found with {self._format_filter_info()}\n"
                                    f"Available serial ports:\n{self.format_comports(ports)}")
        elif len(matched_ports) > 1:
            if self._no_input:
                raise self.ResolveError(f"Multiple com ports are found with {self._format_filter_info()}\n"
                                        f"Found serial ports:\n{self.format_comports(matched_ports)}")
            print(f"Found {len(matched_ports)} serial ports")
            for i, port in enumerate(matched_ports, start=1):
                print(f"{i} - {self.port_description(port)}")
            while True:
                choice = input(f'Please enter a port number [1-{len(matched_ports)}]: ')
                try:
                    port = matched_ports[int(choice) - 1]
                except Exception:
                    print(f"Invalid input \"{choice}\"!")
                else:
                    break
        else:
            port = matched_ports[0]

        return port.device


_DEFAULT_BAUDRATE = 9600


def main(args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('port', nargs='?', help='Serial port')
    parser.add_argument('--baudrate', type=int, default=_DEFAULT_BAUDRATE, help='Serial port baud rate')
    parser.add_argument('--eol', default='lf', help='End of line transformation', choices=['crlf', 'lf', 'cr'])
    parser.add_argument('--filter', nargs='*',
                        help=f'Serial port filter expression\n{SerialPortSearcher.format_filter_help()}')
    parser.add_argument('--no-input', action='store_true', help="Don't user interactive input to resolve port")
    parser.add_argument('--list-ports', action='store_true', help='list ports instead of terminal running')

    parsed_args = parser.parse_args(args)
    device = parsed_args.port

    # configure logger
    logging.basicConfig(level=logging.INFO, format='%(asctime)-15s [%(name)s] %(levelname)s: %(message)s')

    if parsed_args.list_ports:
        ports = SerialPortSearcher(filter_info=parsed_args.filter).list_comports()
        for i, port in enumerate(ports, start=1):
            print(f'{i} - {SerialPortSearcher.port_description(port)}')
        sys.exit(0)

    if device is None:
        # resolve device
        device = SerialPortSearcher(filter_info=parsed_args.filter, no_input=parsed_args.no_input).resolve()

    sys.exit(_miniterm_entrypoint(
        device=device,
        baudrate=parsed_args.baudrate,
        eol=parsed_args.eol
    ))
