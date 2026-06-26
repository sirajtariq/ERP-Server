"""
Custom pagination classes for the sales module.

Supports client-controlled page size via ``page_size`` query parameter,
capped at 100 to prevent excessive payloads.
"""

import math

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class CustomPageNumberPagination(PageNumberPagination):
    """
    Pagination that accepts ``page`` and ``page_size`` query parameters.

    Query parameters:
        page      – page number (default: 1)
        page_size – results per page (default: 10, max: 100)
    """

    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        total_pages = math.ceil(self.page.paginator.count / self.get_page_size(self.request))
        return Response(
            {
                "count": self.page.paginator.count,
                "total_pages": total_pages,
                "page": self.page.number,
                "page_size": self.get_page_size(self.request),
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "results": data,
            }
        )
