from decimal import Decimal
from rest_framework import serializers

from inventory.models import Item, StockMovement
import inventory.services as services


class StockMovementSerializer(serializers.ModelSerializer):
    """
    Serializer for StockMovement records.
    Used for stock audit logs and movement history tables.
    """
    item_code = serializers.CharField(source='item.item_code', read_only=True)
    item_name = serializers.CharField(source='item.name', read_only=True)
    unit = serializers.CharField(source='item.unit', read_only=True)

    class Meta:
        model = StockMovement
        fields = [
            'id',
            'item',
            'item_code',
            'item_name',
            'unit',
            'date',
            'type',
            'quantity',
            'reason',
            'notes',
            'stock_before',
            'stock_after',
            'reference_type',
            'reference_id',
            'created_at',
        ]
        read_only_fields = fields


class ItemListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for Inventory Items table view (GET /api/inventory/items/).
    Excludes heavy text/date fields to optimize table load times.
    All dynamic metrics are computed exclusively via services.calculate_item_list_metrics.
    Outputs strict camelCase JSON keys.
    """
    itemCode = serializers.CharField(source='item_code', read_only=True)
    purchaseRate = serializers.DecimalField(source='purchase_rate', max_digits=12, decimal_places=2, read_only=True)
    saleRate = serializers.DecimalField(source='sale_rate', max_digits=12, decimal_places=2, read_only=True)
    profitMarginPct = serializers.SerializerMethodField()
    currentStock = serializers.SerializerMethodField()
    stockValue = serializers.SerializerMethodField()
    stockStatus = serializers.SerializerMethodField()

    class Meta:
        model = Item
        fields = [
            'id',
            'itemCode',
            'name',
            'category',
            'unit',
            'purchaseRate',
            'saleRate',
            'profitMarginPct',
            'currentStock',
            'stockValue',
            'stockStatus',
        ]
        read_only_fields = fields

    def _get_metrics(self, obj) -> dict:
        if not hasattr(obj, '_cached_list_metrics'):
            obj._cached_list_metrics = services.calculate_item_list_metrics(obj)
        return obj._cached_list_metrics

    def get_profitMarginPct(self, obj) -> float:
        return self._get_metrics(obj)['profit_margin_pct']

    def get_currentStock(self, obj) -> str:
        return f"{self._get_metrics(obj)['current_stock']:.2f}"

    def get_stockValue(self, obj) -> str:
        return f"{self._get_metrics(obj)['stock_value']:.2f}"

    def get_stockStatus(self, obj) -> str:
        return self._get_metrics(obj)['stock_status']


class ItemSerializer(serializers.ModelSerializer):
    """
    Serializer for Item creation, update, and detail views.
    Enforces unique itemCode validation among active items (is_deleted=False).
    Automatically records opening stock movement on creation if openingStock > 0.
    Accepts both camelCase and snake_case input payload fields in POST/PUT/PATCH calls.
    Outputs strict camelCase JSON response payloads.
    """
    itemCode = serializers.CharField(source='item_code')
    purchaseRate = serializers.DecimalField(source='purchase_rate', max_digits=12, decimal_places=2, default=Decimal('0.00'), required=False)
    saleRate = serializers.DecimalField(source='sale_rate', max_digits=12, decimal_places=2, default=Decimal('0.00'), required=False)
    openingStock = serializers.DecimalField(source='opening_stock', max_digits=12, decimal_places=2, default=Decimal('0.00'), required=False)
    minStock = serializers.DecimalField(source='min_stock', max_digits=12, decimal_places=2, default=Decimal('0.00'), required=False)
    isDeleted = serializers.BooleanField(source='is_deleted', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    currentStock = serializers.SerializerMethodField()
    stockStatus = serializers.SerializerMethodField()
    stockValue = serializers.SerializerMethodField()

    class Meta:
        model = Item
        fields = [
            'id',
            'itemCode',
            'name',
            'category',
            'unit',
            'purchaseRate',
            'saleRate',
            'openingStock',
            'minStock',
            'description',
            'isDeleted',
            'createdAt',
            'updatedAt',
            'currentStock',
            'stockStatus',
            'stockValue',
        ]
        read_only_fields = ['id', 'isDeleted', 'createdAt', 'updatedAt', 'currentStock', 'stockStatus', 'stockValue']

    def to_internal_value(self, data):
        data = data.copy()
        if 'item_code' in data and 'itemCode' not in data:
            data['itemCode'] = data['item_code']
        if 'purchase_rate' in data and 'purchaseRate' not in data:
            data['purchaseRate'] = data['purchase_rate']
        if 'sale_rate' in data and 'saleRate' not in data:
            data['saleRate'] = data['sale_rate']
        if 'opening_stock' in data and 'openingStock' not in data:
            data['openingStock'] = data['opening_stock']
        if 'min_stock' in data and 'minStock' not in data:
            data['minStock'] = data['min_stock']
        return super().to_internal_value(data)

    def get_currentStock(self, obj) -> str:
        return f"{services.get_item_current_stock(obj):.2f}"

    def get_stockStatus(self, obj) -> str:
        summary = services.calculate_item_summary(obj)
        return summary['stock_status']

    def get_stockValue(self, obj) -> str:
        summary = services.calculate_item_summary(obj)
        return f"{summary['stock_value']:.2f}"

    def validate_itemCode(self, value):
        code = value.strip()
        queryset = Item.objects.filter(item_code__iexact=code, is_deleted=False)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError(
                f"Item code '{code}' pehle se mojood hai. Barah-e-karam koi doosra unique code enter karein."
            )
        return code

    def create(self, validated_data):
        item = Item.objects.create(**validated_data)
        if item.opening_stock > Decimal('0.00'):
            services.record_stock_movement(
                item=item,
                movement_type='in',
                quantity=item.opening_stock,
                reason='Opening Stock',
                ref_type='opening'
            )
        return item


class ItemDetailSerializer(serializers.ModelSerializer):
    """
    Flat camelCase Item Detail serializer for GET /api/inventory/items/{id}/
    All calculated metrics come directly from services.calculate_item_summary(item).
    """
    itemName = serializers.CharField(source='name', read_only=True)
    itemCode = serializers.CharField(source='item_code', read_only=True)
    itemStatus = serializers.SerializerMethodField()
    minStock = serializers.DecimalField(source='min_stock', max_digits=12, decimal_places=2, read_only=True)
    openingStock = serializers.DecimalField(source='opening_stock', max_digits=12, decimal_places=2, read_only=True)
    purchaseRate = serializers.DecimalField(source='purchase_rate', max_digits=12, decimal_places=2, read_only=True)
    saleRate = serializers.DecimalField(source='sale_rate', max_digits=12, decimal_places=2, read_only=True)
    totalIn = serializers.SerializerMethodField()
    totalOut = serializers.SerializerMethodField()
    currentStock = serializers.SerializerMethodField()
    profitPerItem = serializers.SerializerMethodField()
    stockValue = serializers.SerializerMethodField()
    profitMargin = serializers.SerializerMethodField()

    class Meta:
        model = Item
        fields = [
            'id',
            'itemName',
            'itemCode',
            'itemStatus',
            'category',
            'unit',
            'minStock',
            'description',
            'openingStock',
            'totalIn',
            'totalOut',
            'currentStock',
            'profitPerItem',
            'stockValue',
            'purchaseRate',
            'saleRate',
            'profitMargin',
        ]
        read_only_fields = fields

    def _get_summary(self, obj) -> dict:
        if not hasattr(obj, '_cached_summary'):
            obj._cached_summary = services.calculate_item_summary(obj)
        return obj._cached_summary

    def get_itemStatus(self, obj) -> str:
        return self._get_summary(obj)['stock_status']

    def get_totalIn(self, obj) -> str:
        return f"{self._get_summary(obj)['total_in']:.2f}"

    def get_totalOut(self, obj) -> str:
        return f"{self._get_summary(obj)['total_out']:.2f}"

    def get_currentStock(self, obj) -> str:
        return f"{self._get_summary(obj)['current_stock']:.2f}"

    def get_profitPerItem(self, obj) -> str:
        return f"{self._get_summary(obj)['profit_per_unit']:.2f}"

    def get_stockValue(self, obj) -> str:
        return f"{self._get_summary(obj)['stock_value']:.2f}"

    def get_profitMargin(self, obj) -> float:
        return self._get_summary(obj)['profit_margin_pct']


class StockMovementHistorySerializer(serializers.ModelSerializer):
    """
    Serializer for item stock movement history log in Item Detail modal.
    Outputs flat camelCase JSON keys.
    """
    note = serializers.CharField(source='notes', allow_null=True, read_only=True)
    stockBefore = serializers.DecimalField(source='stock_before', max_digits=12, decimal_places=2, read_only=True)
    stockAfter = serializers.DecimalField(source='stock_after', max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = StockMovement
        fields = [
            'id',
            'date',
            'type',
            'quantity',
            'reason',
            'note',
            'stockBefore',
            'stockAfter',
        ]
        read_only_fields = fields


class StockAdjustmentSerializer(serializers.Serializer):
    """
    Serializer for manually adjusting stock on an item.
    Supports either 'qty' or 'quantity' input fields for frontend compatibility.
    Calls services.record_stock_movement under the hood.
    """
    type = serializers.ChoiceField(choices=['in', 'out'])
    qty = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    reason = serializers.CharField(max_length=100)
    date = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate(self, data):
        qty = data.get('qty') or data.get('quantity')
        if qty is None or qty <= Decimal('0.00'):
            raise serializers.ValidationError({"qty": "Quantity must be greater than zero."})
        data['final_quantity'] = qty
        return data

    def save_adjustment(self, item: Item) -> StockMovement:
        validated_data = self.validated_data
        movement = services.record_stock_movement(
            item=item,
            movement_type=validated_data['type'],
            quantity=validated_data['final_quantity'],
            reason=validated_data['reason'],
            date=validated_data.get('date'),
            notes=validated_data.get('notes'),
            ref_type='manual'
        )
        return movement
