ORDERS = {
    "BK-10042": {
        "id": "BK-10042",
        "customer_name": "Jane Doe",
        "customer_email": "jane.doe@email.com",
        "status": "shipped",
        "order_date": "2026-09-10",
        "shipped_date": "2026-09-12",
        "estimated_delivery": "2026-09-22",
        "tracking_number": "1Z999AA10123456784",
        "carrier": "UPS",
        "items": [
            {"title": "The Midnight Library", "author": "Matt Haig", "qty": 1, "price": 14.99},
            {"title": "Atomic Habits", "author": "James Clear", "qty": 1, "price": 18.99},
        ],
        "total": 33.98,
        "eligible_for_return": True,
    },
    "BK-9871": {
        "id": "BK-9871",
        "customer_name": "John Smith",
        "customer_email": "john.smith@email.com",
        "status": "processing",
        "order_date": "2026-09-19",
        "shipped_date": None,
        "estimated_delivery": "2026-09-26",
        "tracking_number": None,
        "carrier": None,
        "items": [
            {"title": "Dune", "author": "Frank Herbert", "qty": 2, "price": 12.99},
        ],
        "total": 25.98,
        "eligible_for_return": False,
        "return_ineligible_reason": "Order is still processing and hasn't shipped. You can cancel it instead.",
    },
    "BK-8823": {
        "id": "BK-8823",
        "customer_name": "Alex Johnson",
        "customer_email": "alex.j@email.com",
        "status": "delivered",
        "order_date": "2026-08-15",
        "shipped_date": "2026-08-17",
        "delivered_date": "2026-08-20",
        "tracking_number": "9400111899223397990185",
        "carrier": "USPS",
        "items": [
            {"title": "Project Hail Mary", "author": "Andy Weir", "qty": 1, "price": 16.99},
        ],
        "total": 16.99,
        "eligible_for_return": False,
        "return_ineligible_reason": "Outside the 30-day return window (ordered 2026-08-15).",
    },
    "BK-10105": {
        "id": "BK-10105",
        "customer_name": "Sarah Lee",
        "customer_email": "sarah.lee@email.com",
        "status": "delivered",
        "order_date": "2026-09-08",
        "shipped_date": "2026-09-09",
        "delivered_date": "2026-09-13",
        "tracking_number": "1Z999AA10123456999",
        "carrier": "UPS",
        "items": [
            {"title": "The Name of the Wind", "author": "Patrick Rothfuss", "qty": 1, "price": 15.99},
        ],
        "total": 15.99,
        "eligible_for_return": True,
    },
}

# In-memory write logs — simulate backend mutations
REFUNDS_INITIATED: dict = {}
CONFIRMATIONS_RECORDED: dict = {}  # order_id → customer_email

POLICIES = {
    "shipping": """Shipping Policy
- Standard: 5–7 business days, free on orders over $35
- Expedited: 2–3 business days, $9.99
- Overnight: next business day, $24.99
- International: 10–21 business days, rates vary
- Orders placed before 2 PM EST ship same day (Mon–Fri)""",

    "returns": """Return & Refund Policy
- 30-day return window from order date
- Books must be unread, unmarked, and undamaged
- Digital/eBook purchases are non-refundable
- Refunds process in 5–7 business days to original payment method
- Return shipping is the customer's responsibility unless item was defective or wrong""",

    "password": """Password Reset
- Go to bookly.com/login → "Forgot Password"
- Enter your account email; link is valid 24 hours
- Check spam if no email arrives
- For lockouts, email support@bookly.com""",

    "payment": """Payment & Billing
- Accepted: Visa, Mastercard, Amex, PayPal, Bookly Gift Cards
- Statement shows as "BOOKLY INC"
- Tax calculated at checkout by shipping address
- Billing disputes: support@bookly.com with order number""",

    "cancellation": """Order Cancellation
- Cancel within 1 hour of placement if status is "processing"
- Once shipped, cancellation is unavailable — return after delivery
- Refunded within 3–5 business days""",
}
