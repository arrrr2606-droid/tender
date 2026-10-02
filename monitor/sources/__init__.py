from .b2b_center import B2BCenter
from .eis import EIS
from .fabrikant import Fabrikant
from .fedresurs import Fedresurs
from .torgi_gov import TorgiGov

# Порядок важен: ЕИС первым, чтобы дубли с других площадок склеивались по номеру ЕИС.
ALL = [EIS, TorgiGov, Fedresurs, B2BCenter, Fabrikant]
