from .custom_pages import CustomPages
from .magicbricks import MagicBricks
from .nobroker import NoBroker
from .squareyards import SquareYards

ALL = [NoBroker(), MagicBricks(), SquareYards(), CustomPages()]
BY_KEY = {s.key: s for s in ALL}
