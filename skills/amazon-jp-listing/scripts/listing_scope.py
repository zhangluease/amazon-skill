"""Input boundary shared by the single-product Listing tools."""

GROUP_FIELDS = frozenset((
    'variants', 'parent', 'parent_sku', 'shared_content', 'variation_dimensions',
    'variation_theme', 'appearance_assessment', 'title_template',
    'item_highlight_template', 'content_overrides', 'variant_bindings',
    'image_sets', 'media_strategy',
))


def require_single_product(*records):
    """Reject grouped inputs before normalization, output writes or backend calls."""
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('Product profile and Listing must be JSON objects')
        if record.get('listing_mode', 'single') != 'single' or GROUP_FIELDS.intersection(record):
            raise ValueError('Only one product is supported per run; split products into separate tasks')
        for key in ('image_plan', 'a_plus_plan', 'aplus_plan'):
            plans = record.get(key, [])
            if isinstance(plans, list) and any(isinstance(plan, dict) and 'variant_bindings' in plan for plan in plans):
                raise ValueError('Image plans must describe this product directly; split products into separate tasks')
