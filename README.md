# AVS-Shipday Integration

A Python service that integrates Address Verification System (AVS) with Shipday delivery management. Verify delivery addresses using Google Maps Geocoding API and create orders in Shipday with validated location data.

## Features

- **Address Verification**: Validate and standardize addresses using Google Maps Geocoding API
- **Confidence Scoring**: Get accuracy scores for address verification
- **Shipday Integration**: Create and manage delivery orders
- **Photo Proof of Delivery**: Upload delivery photos and signatures
- **Webhook Support**: Handle Shipday events for real-time updates
- **Batch Processing**: Verify multiple addresses in a single request
- **REST API**: Full-featured HTTP API with authentication

## Quick Start

### 1. Clone and Setup

```bash
git clone <your-repo-url>
cd avs-shipday-integration
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

Required environment variables:
- `GOOGLE_MAPS_API_KEY`: Your Google Maps Geocoding API key
- `SHIPDAY_API_KEY`: Your Shipday API key

### 3. Run Locally

```bash
python flask_service_enhanced.py
```

The service will start at `http://localhost:5000`

## API Endpoints

### Health Check
```
GET /health
```

### Address Verification
```
POST /api/v1/verify-address
Content-Type: application/json

{
    "address": "1600 Amphitheatre Parkway, Mountain View, CA"
}
```

Response:
```json
{
    "success": true,
    "original_address": "1600 Amphitheatre Parkway, Mountain View, CA",
    "confidence_score": 1.0,
    "verified_address": {
        "street": "1600 Amphitheatre Parkway",
        "city": "Mountain View",
        "state": "CA",
        "zip_code": "94043",
        "country": "US",
        "latitude": 37.4224764,
        "longitude": -122.0842499
    },
    "formatted_address": "1600 Amphitheatre Parkway, Mountain View, CA 94043"
}
```

### Batch Address Verification
```
POST /api/v1/verify-address/batch
Content-Type: application/json

{
    "addresses": [
        "123 Main St, San Francisco, CA",
        "456 Oak Ave, Los Angeles, CA"
    ]
}
```

### Create Order
```
POST /api/v1/orders
Content-Type: application/json
Authorization: Bearer <your-api-key>

{
    "order_number": "ORD-12345",
    "customer": {
        "name": "John Doe",
        "phone": "+1234567890",
        "email": "john@example.com"
    },
    "delivery_address": "123 Main St, San Francisco, CA 94102",
    "items": [
        {"name": "Pizza", "quantity": 1, "price": 15.00}
    ],
    "special_instructions": "Ring doorbell twice"
}
```

### Get Order Status
```
GET /api/v1/orders/{order_id}
Authorization: Bearer <your-api-key>
```

### Update Order Status
```
PUT /api/v1/orders/{order_id}/status
Content-Type: application/json
Authorization: Bearer <your-api-key>

{
    "status": "DELIVERED",
    "notes": "Left at front door"
}
```

### Upload Delivery Photo
```
POST /api/v1/orders/{order_id}/photos
Content-Type: application/json
Authorization: Bearer <your-api-key>

{
    "photo_url": "https://storage.example.com/delivery-proof.jpg",
    "photo_type": "proof_of_delivery"
}
```

### Complete Delivery
```
POST /api/v1/orders/{order_id}/complete
Content-Type: application/json
Authorization: Bearer <your-api-key>

{
    "photo_url": "https://storage.example.com/delivery-proof.jpg",
    "signature_url": "https://storage.example.com/signature.jpg",
    "notes": "Delivered successfully"
}
```

## Deployment

### Deploy to Render

1. Connect your GitHub repository to Render
2. Create a new Web Service
3. Use the `render.yaml` blueprint or configure manually:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn flask_service_enhanced:app --bind 0.0.0.0:$PORT --workers 2`
4. Add environment variables in the Render dashboard

### Deploy to Railway

1. Connect your GitHub repository to Railway
2. Railway will auto-detect the Python project
3. Add environment variables in the Railway dashboard
4. Deploy

### Environment Variables for Production

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_MAPS_API_KEY` | Yes | Google Maps Geocoding API key |
| `SHIPDAY_API_KEY` | Yes | Shipday API key |
| `API_KEY` | No | API key for service authentication |
| `SHIPDAY_WEBHOOK_SECRET` | No | Secret for webhook verification |
| `PORT` | No | Server port (default: 5000) |
| `CORS_ORIGINS` | No | Allowed CORS origins (default: *) |
| `FLASK_DEBUG` | No | Enable debug mode (default: false) |

## Webhooks

Configure your Shipday webhook URL to: `https://your-domain.com/webhooks/shipday`

Supported events:
- `order.status.changed`
- `order.assigned`
- `order.picked_up`
- `order.delivered`
- `carrier.location.updated`

## Python SDK Usage

```python
from avs_shipday_integration import AVSShipdayIntegration

# Initialize
integration = AVSShipdayIntegration(
    google_api_key="your_google_key",
    shipday_api_key="your_shipday_key"
)

# Create a verified delivery
result = integration.create_verified_delivery(
    customer_name="John Doe",
    phone="+1234567890",
    email="john@example.com",
    address="123 Main St, San Francisco, CA",
    items=[{"name": "Pizza", "quantity": 1, "price": 15.00}],
    order_number="ORD-12345",
    special_instructions="Leave at door"
)

if result["success"]:
    print(f"Order created: {result['order']}")
    print(f"Address confidence: {result['verification']['confidence_score']}")
else:
    print(f"Failed: {result['error']}")
```

## Testing

```bash
# Run tests
pytest

# Run with coverage
pytest --cov=. --cov-report=html
```

## License

MIT License
