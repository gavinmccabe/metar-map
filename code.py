from adafruit_aw9523 import AW9523, DigitalInOut
from adafruit_requests import Session
import board
import busio
import os
import re
import socketpool
import ssl
import sys
import time
import wifi

LED_BRIGHTNESS = float(os.getenv("LED_BRIGHTNESS"))

# Constants for LED colors
class LEDColors:
    GREEN = 0x00FF00
    RED = 0xFF0000
    BLUE = 0x0000FF
    PURPLE = 0xFF00FF
    YELLOW = 0xFF2200
    WIFI_BLUE = 0x00F9E9

class I2CController:
    """Class representing an I2C controller."""

    def __init__(self, scl: board.Pin, sda: board.Pin):
        """
        Initialize the I2C controller.

        Args:
            scl (board.Pin): The SCL pin.
            sda (board.Pin): The SDA pin.
        """
        self.controller = busio.I2C(scl=scl, sda=sda)
        self.scl = scl
        self.sda = sda

class LEDDriverBoard:
    """Class representing an LED driver board."""

    def __init__(self, controller: I2CController, addr: int):
        """
        Initialize the LED driver board.

        Args:
            controller (I2CController): The I2C controller.
            addr (int): The address of the LED driver board.
        """
        self.controller = controller
        self.addr = addr
        self.board = AW9523(controller.controller, addr)
        self.board.LED_modes = 0xffff

    def get_pin(self, pin: int) -> DigitalInOut:
        """
        Get the digital input/output pin.

        Args:
            pin (int): The pin number.

        Returns:
            DigitalInOut: The digital input/output pin.
        """
        return self.board.get_pin(pin)

    def set_constant_current(self, pin: int, value: int) -> None:
        """
        Set the constant current of a pin.

        Args:
            pin (int): The pin number.
            value (int): The constant current value.
        """
        self.board.set_constant_current(pin, value)
        
# Class for managing LED driver boards
class BoardManager:
    def __init__(self, controllers: list[I2CController]):
        self.controllers = controllers
        self.boards: list[LEDDriverBoard] = []

    def add_board(self, controller: I2CController, addr: int) -> None:
        board = LEDDriverBoard(controller, addr)
        self.boards.append(board)

    def get_board(self, controller_index: int, addr: int) -> LEDDriverBoard:
        for board in self.boards:
            if board.controller == self.controllers[controller_index] and board.addr == addr:
                return board
        return None

class RGBLED:
    """Class representing an RGB LED."""

    def __init__(self, host: LEDDriverBoard, red_pin: int, green_pin: int, blue_pin: int):
        """
        Initialize the RGB LED.

        Args:
            host (LEDDriverBoard): The LED driver board hosting the LED.
            red_pin (int): The pin number for the red channel.
            green_pin (int): The pin number for the green channel.
            blue_pin (int): The pin number for the blue channel.
        """
        self.red_pin_num = red_pin
        self.green_pin_num = green_pin
        self.blue_pin_num = blue_pin
        self.red_pin = host.get_pin(red_pin)
        self.green_pin = host.get_pin(green_pin)
        self.blue_pin = host.get_pin(blue_pin)
        self.host = host

        self.red_pin.switch_to_output(True)
        self.green_pin.switch_to_output(True)
        self.blue_pin.switch_to_output(True)

    def set_color(self, color: int, brightness: float):
        """
        Set the color and brightness of the LED.

        Args:
            color (int): The RGB color value.
            brightness (float): The brightness level.
        """
        r = color >> 16
        g = (color & 0x00FF00) >> 8
        b = color & 0x0000FF
        self.host.set_constant_current(self.red_pin_num, int(r * brightness))
        self.host.set_constant_current(self.green_pin_num, int(g * brightness))
        self.host.set_constant_current(self.blue_pin_num, int(b * brightness))

class FlightCategory:
    """Enum representing flight categories."""
    UNKNOWN = "UNKNOWN"
    VFR = "VFR"
    MVFR = "MVFR"
    IFR = "IFR"
    LIFR = "LIFR"

    @staticmethod
    def from_string(name: str) -> FlightCategory:
        """
        Get the FlightCategory enum from its string representation.

        Args:
            name (str): The string representation of the flight category.

        Returns:
            FlightCategory: The FlightCategory enum.
        """
        str_map = {
            "UNKNOWN": FlightCategory.UNKNOWN,
            "VFR": FlightCategory.VFR,
            "MVFR": FlightCategory.MVFR,
            "IFR": FlightCategory.IFR,
            "LIFR": FlightCategory.LIFR,
        }
        return str_map.get(name, FlightCategory.UNKNOWN)

class Airport:
    """Class representing an airport."""

    def __init__(self, airport_code: str, red_pin: int, green_pin: int, blue_pin: int, alternate: str, host: LEDDriverBoard):
        """
        Initialize the airport.

        Args:
            airport_code (str): The code of the airport.
            red_pin (int): The pin number for the red channel of the LED.
            green_pin (int): The pin number for the green channel of the LED.
            blue_pin (int): The pin number for the blue channel of the LED.
            host (LEDDriverBoard): The LED driver board hosting the LED.
        """
        self.airport_code = airport_code
        self.led = RGBLED(host, red_pin, green_pin, blue_pin)
        self.led.set_color(LEDColors.YELLOW, LED_BRIGHTNESS)
        self.flight_category = FlightCategory.UNKNOWN
        self.alternate = alternate

    @staticmethod
    def from_config_line(line: str, board_manager: BoardManager) -> 'Airport':
        """
        Create an Airport instance from a configuration line.

        Args:
            line (str): The configuration line.
            board_manager (BoardManager): The board manager.

        Returns:
            Airport: The created Airport instance.
        """
        split_line = line.split()
        host = board_manager.get_board(int(split_line[1]), int(split_line[2], 16))
        return Airport(split_line[0], int(split_line[3]), int(split_line[4]), int(split_line[5]), split_line[6], host)

    @staticmethod
    def _get_color_from_flight_category(category: FlightCategory) -> int:
        """
        Get the color corresponding to the flight category.

        Args:
            category (FlightCategory): The flight category.

        Returns:
            int: The RGB color value.
        """
        color_map = {
            "VFR": LEDColors.GREEN,
            "MVFR": LEDColors.BLUE,
            "IFR": LEDColors.RED,
            "LIFR": LEDColors.PURPLE,
            "UNKNOWN": LEDColors.YELLOW,
        }
        return color_map.get(category, LEDColors.YELLOW)

    def update_flight_category(self, requests_session: Session) -> None:
        """
        Update the flight category of the airport.

        Args:
            requests_session (Session): The session for making HTTP requests.
        """
        resp = None
        try:
            resp = requests_session.get(f"https://aviationweather.gov/api/data/metar?ids={self.airport_code}&format=xml")
            flight_category = re.search("<flight_category>(.*)</flight_category>", resp.text).group(1)
            flight_category = FlightCategory.from_string(flight_category)
            if flight_category == FlightCategory.UNKNOWN:
                raise Exception
        except Exception as ex:
            resp.close() if resp else ...
            # Try alternate
            try:
                print(f"Trying alternate for {self.airport_code} ({self.alternate})")
                resp = requests_session.get(f"https://aviationweather.gov/api/data/metar?ids={self.alternate}&format=xml")
                flight_category = re.search("<flight_category>(.*)</flight_category>", resp.text).group(1)
                flight_category = FlightCategory.from_string(flight_category)
            except Exception as ex:
                flight_category = FlightCategory.UNKNOWN
                print(f"[Error ({self.airport_code})] {ex}")
        finally:
            if resp:
                resp.close()

        if flight_category != self.flight_category:
            self.flight_category = flight_category
            self.led.set_color(self._get_color_from_flight_category(flight_category), LED_BRIGHTNESS)

class AirportManager:
    """Class managing airports."""

    def __init__(self, requests: Session):
        """
        Initialize the airport manager.

        Args:
            requests (Session): The session for making HTTP requests.
        """
        self.requests_session = requests
        self.airport_list: list[Airport] = []

    def load_airports(self, filename: str, board_manager: 'BoardManager') -> None:
        """
        Load airports from a configuration file.

        Args:
            filename (str): The path to the configuration file.
            board_manager (BoardManager): The board manager.
        """
        with open(filename) as f:
            lines = f.readlines()
        for line in lines:
            self.airport_list.append(Airport.from_config_line(line, board_manager))

    def update_airport_flight_categories(self) -> None:
        """Update the flight categories of all airports."""
        for airport in self.airport_list:
            airport.update_flight_category(self.requests_session)

class Map:
    """Class representing a map of airports."""

    def __init__(self, airport_manager: AirportManager):
        """
        Initialize the map.

        Args:
            airport_manager (AirportManager): The airport manager.
        """
        self.airport_manager = airport_manager

    def show_color(self, color: int, brightness: float) -> None:
        """
        Show a color on all airports.

        Args:
            color (int): The RGB color value.
            brightness (float): The brightness level.
        """
        for airport in self.airport_manager.airport_list:
            airport.led.set_color(color, brightness)

    def show_error_state(self, error: str) -> None:
        """
        Show an error state on all airports.

        Args:
            error (str): The error message.
        """
        self.show_color(LEDColors.YELLOW, LED_BRIGHTNESS)
        print(f"[Error] {error}")

    def connect_wifi(self) -> bool:
        """
        Connect to WiFi.

        Returns:
            bool: True if connected successfully, False otherwise.
        """
        for attempt in range(1, 6):
            self.show_color(LEDColors.WIFI_BLUE, LED_BRIGHTNESS)
            print(f"Attempting to connect to WIFI (attempt {attempt})...")
            try:
                wifi.radio.connect(os.getenv("CIRCUITPY_WIFI_SSID"), os.getenv("CIRCUITPY_WIFI_PASSWORD"))
                print("Connected to WIFI")
                return True
            except ConnectionError:
                time.sleep(1)
                continue
        else:
            return False

def main():
    i2c0 = I2CController(board.GP1, board.GP0)
    i2c1 = I2CController(board.GP27, board.GP26)

    board_manager = BoardManager([i2c0, i2c1])
    for controller in [i2c0, i2c1]:
        for addr in [0x58, 0x59, 0x5A, 0x5B]:
            board_manager.add_board(controller, addr)

    pool = socketpool.SocketPool(wifi.radio)
    requests = Session(pool, ssl.create_default_context())

    manager = AirportManager(requests)
    manager.load_airports("./lib/config.txt", board_manager)
    map_manager = Map(manager)

    wifi_result = map_manager.connect_wifi()
    if wifi_result:
        map_manager.show_color(LEDColors.GREEN, LED_BRIGHTNESS)
    else:
        map_manager.show_color(LEDColors.RED, LED_BRIGHTNESS)
        sys.exit(1)

    time.sleep(1)
    map_manager.show_color(LEDColors.YELLOW, LED_BRIGHTNESS)

    while True:
        manager.update_airport_flight_categories()
        time.sleep(45)

if __name__ == "__main__":
    main()