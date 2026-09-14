from sopara.api.app import production_app
from sopara.api.config import ControlPlaneConfig

app = production_app(ControlPlaneConfig.from_environment())
