# generated by datamodel-codegen:
#   filename:  https://raw.githubusercontent.com/HumanBrainProject/openMINDS/3fa86f956b407b2debf47c2e1b6314e37579c707/v3/core/v4/miscellaneous/quantitativeValueRange.schema.json

from typing import Any, Dict, Optional

from pydantic import Field
from siibra.openminds.base import SiibraBaseModel


class Model(SiibraBaseModel):
    id: str = Field(..., alias='@id', description='Metadata node identifier.')
    type: str = Field(..., alias='@type')
    max_value: float = Field(
        ...,
        alias='maxValue',
        description='Greatest quantity attained or allowed.',
        title='maxValue',
    )
    max_value_unit: Optional[Dict[str, Any]] = Field(
        None,
        alias='maxValueUnit',
        title='maxValueUnit',
    )
    min_value: float = Field(
        ...,
        alias='minValue',
        description='Smallest quantity attained or allowed.',
        title='minValue',
    )
    min_value_unit: Optional[Dict[str, Any]] = Field(
        None,
        alias='minValueUnit',
        title='minValueUnit',
    )
