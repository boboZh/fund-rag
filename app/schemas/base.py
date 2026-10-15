# CamelModel,配置alias_generator,

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

class CamelModel(BaseModel):
  model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)