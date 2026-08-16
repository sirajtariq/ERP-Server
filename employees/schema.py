"""
OpenAPI Schema helper module providing @extend_schema decorator for employees app views.
Translates @extend_schema parameters cleanly to drf_yasg's swagger_auto_schema.
"""

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema


def extend_schema(
    summary=None,
    description=None,
    parameters=None,
    request=None,
    responses=None,
    tags=None,
    operation_description=None,
    manual_parameters=None,
    **kwargs
):
    op_desc = description or operation_description or summary
    params = parameters or manual_parameters
    req_body = request or kwargs.get("request_body")

    return swagger_auto_schema(
        operation_description=op_desc,
        manual_parameters=params,
        request_body=req_body,
        responses=responses,
        tags=tags,
        **{k: v for k, v in kwargs.items() if k not in ["operation_description", "manual_parameters", "request_body"]}
    )
