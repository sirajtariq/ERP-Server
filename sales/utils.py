from rest_framework import serializers
from .models import Customer

def get_or_create_customer_from_data(data: dict) -> Customer:
    if not isinstance(data, dict):
        raise serializers.ValidationError(
            "customer_data must be an object with customer_name, phone, customer_type, and optionally customer_id/tax_number."
        )

    customer_name = (data.get('customer_name') or '').strip()
    if not customer_name:
        customer_name = "General"
    phone = (data.get('phone') or '').strip()
    customer_type = data.get('customer_type')
    tax_number = data.get('tax_number') or None

    if not phone:
        raise serializers.ValidationError("customer_data.phone is required.")
        
    existing = Customer.all_objects.filter(phone=phone).first()
    if existing:
        if getattr(existing, 'is_deleted', False):
            existing.restore()
        return existing

    if customer_type != 'walkin':
        raise serializers.ValidationError(
            "customer_data.customer_type must be 'walkin' — invoice creation can only generate walk-in customers."
        )

    return Customer.objects.create(
        customer_name=customer_name,
        customer_type='walkin',
        phone=phone,
        tax_number=tax_number,
    )
