from django.db.models import Q
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from rest_framework.pagination import PageNumberPagination

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from inventory.models import Item, StockMovement
from inventory.serializers import (
    ItemSerializer,
    ItemListSerializer,
    ItemDetailSerializer,
    StockMovementSerializer,
    StockMovementHistorySerializer,
    StockAdjustmentSerializer,
)
import inventory.services as services


class InventoryPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class ItemViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing inventory items.
    Enforces soft-deletion, single-source-of-truth service calculations,
    and returns global summary KPIs envelope on list endpoint.
    """
    queryset = Item.objects.filter(is_deleted=False).order_by('-id')
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = InventoryPagination

    def get_serializer_class(self):
        if self.action == 'list':
            return ItemListSerializer
        elif self.action == 'retrieve':
            return ItemDetailSerializer
        return ItemSerializer

    def get_queryset(self):
        qs = Item.objects.filter(is_deleted=False).order_by('-id')
        params = self.request.query_params

        name = params.get('name', '').strip()
        if name:
            qs = qs.filter(name__icontains=name)

        code = params.get('code', '').strip()
        if code:
            qs = qs.filter(item_code__icontains=code)

        category = params.get('category', '').strip()
        if category and category.lower() != 'all':
            qs = qs.filter(category__iexact=category)

        search = params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(item_code__icontains=search)
            )

        status_param = params.get('status') or params.get('stock_status')
        if status_param:
            status_val = status_param.strip().lower()
            if status_val and status_val != 'all':
                filtered_ids = []
                for item in qs:
                    item_status = services.calculate_item_list_metrics(item)['stock_status']
                    if status_val in ['low', 'low_stock'] and item_status == 'low_stock':
                        filtered_ids.append(item.id)
                    elif status_val in ['out', 'out_of_stock'] and item_status == 'out_of_stock':
                        filtered_ids.append(item.id)
                    elif status_val in ['in', 'in_stock'] and item_status == 'in_stock':
                        filtered_ids.append(item.id)
                qs = qs.filter(id__in=filtered_ids)

        ordering = params.get('ordering', '').strip()
        if ordering:
            allowed_fields = ['name', '-name', 'item_code', '-item_code', 'category', '-category', 'id', '-id', 'created_at', '-created_at']
            if ordering in allowed_fields:
                qs = qs.order_by(ordering)

        return qs

    @swagger_auto_schema(
        operation_description="Retrieve a paginated list of active inventory items with global summary KPIs.",
        manual_parameters=[
            openapi.Parameter(
                "name", openapi.IN_QUERY,
                description="Filter by item name (case-insensitive)",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "code", openapi.IN_QUERY,
                description="Filter by item code (case-insensitive)",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "category", openapi.IN_QUERY,
                description="Filter by category",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "status", openapi.IN_QUERY,
                description="Filter by stock status",
                type=openapi.TYPE_STRING,
                enum=["all", "in", "low", "out"],
            ),
            openapi.Parameter(
                "search", openapi.IN_QUERY,
                description="Universal search (name and code)",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "page", openapi.IN_QUERY,
                description="Page number (default: 1)",
                type=openapi.TYPE_INTEGER,
                default=1,
            ),
            openapi.Parameter(
                "page_size", openapi.IN_QUERY,
                description="Items per page (default: 10)",
                type=openapi.TYPE_INTEGER,
                default=10,
            ),
        ]
    )
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            response_data = response.data
        else:
            serializer = self.get_serializer(queryset, many=True)
            response_data = {
                "count": len(serializer.data),
                "next": None,
                "previous": None,
                "results": serializer.data
            }

        response_data['summary'] = services.calculate_inventory_global_kpis()
        return Response(response_data)

    def perform_destroy(self, instance):
        """
        Soft-delete enforcement: Sets is_deleted=True instead of deleting record.
        """
        instance.is_deleted = True
        instance.save()

    @action(detail=True, methods=['post'], url_path='adjust_stock')
    def adjust_stock(self, request, pk=None):
        item = self.get_object()
        serializer = StockAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        movement = serializer.save_adjustment(item)
        item_summary = services.calculate_item_summary(item)
        formatted_summary = {
            "currentStock": f"{item_summary['current_stock']:.2f}",
            "totalIn": f"{item_summary['total_in']:.2f}",
            "totalOut": f"{item_summary['total_out']:.2f}",
            "stockValue": f"{item_summary['stock_value']:.2f}",
            "profitPerUnit": f"{item_summary['profit_per_unit']:.2f}",
            "profitMarginPct": item_summary["profit_margin_pct"],
            "stockStatus": item_summary["stock_status"],
        }
        return Response(
            {
                "message": "Stock adjusted successfully.",
                "itemSummary": formatted_summary,
                "item_summary": formatted_summary,  # Backwards compatibility alias
                "movement": StockMovementSerializer(movement).data
            },
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'], url_path='adjust')
    def adjust(self, request, pk=None):
        """Alias for adjust_stock for frontend compatibility."""
        return self.adjust_stock(request, pk)

    @swagger_auto_schema(
        operation_description="Retrieve stock movement history for a specific item with optional date range filtering.",
        manual_parameters=[
            openapi.Parameter(
                "page", openapi.IN_QUERY,
                description="Page number (default: 1)",
                type=openapi.TYPE_INTEGER,
                default=1,
            ),
            openapi.Parameter(
                "page_size", openapi.IN_QUERY,
                description="Items per page (default: 10)",
                type=openapi.TYPE_INTEGER,
                default=10,
            ),
            openapi.Parameter(
                "start_date", openapi.IN_QUERY,
                description="Start date for filtering (YYYY-MM-DD)",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "end_date", openapi.IN_QUERY,
                description="End date for filtering (YYYY-MM-DD)",
                type=openapi.TYPE_STRING,
            ),
        ]
    )
    @action(detail=True, methods=['get'], url_path='history')
    def history(self, request, pk=None):
        item = self.get_object()
        qs = item.stock_movements.all().order_by('-date', '-id')

        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        if start_date:
            qs = qs.filter(date__gte=start_date)
        if end_date:
            qs = qs.filter(date__lte=end_date)

        summary = services.calculate_item_summary(item, start_date=start_date, end_date=end_date)

        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = StockMovementHistorySerializer(page, many=True)
            response_data = self.get_paginated_response(serializer.data).data
        else:
            serializer = StockMovementHistorySerializer(qs, many=True)
            response_data = {
                "count": len(serializer.data),
                "next": None,
                "previous": None,
                "results": serializer.data
            }

        response_data['summary'] = {
            "totalIn": f"{summary['total_in']:.2f}",
            "totalOut": f"{summary['total_out']:.2f}",
        }
        return Response(response_data)

    @action(detail=True, methods=['get'], url_path='movements')
    def movements(self, request, pk=None):
        """Alias for history action for frontend compatibility."""
        return self.history(request, pk)


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ReadOnly ViewSet for viewing global stock movement audit logs.
    """
    queryset = StockMovement.objects.select_related('item').filter(item__is_deleted=False).order_by('-created_at', '-id')
    serializer_class = StockMovementSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params

        item_id = params.get('item')
        if item_id:
            qs = qs.filter(item_id=item_id)

        movement_type = params.get('type')
        if movement_type in ['in', 'out']:
            qs = qs.filter(type=movement_type)

        search = params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(item__name__icontains=search) |
                Q(item__item_code__icontains=search) |
                Q(reason__icontains=search) |
                Q(notes__icontains=search)
            )

        return qs
