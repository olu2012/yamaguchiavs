# AVS-Shipday Integration

Bidirectional integration service between Address Verification Service (AVS) and Shipday delivery management platform.

## Features

- **Receive AVS Requests**: Handle incoming address verification requests from AVS and create corresponding Shipday delivery tasks
- **Submit Verification Results**: Submit completed verification results back to AVS with photos and verification details
- **Shipday Integration**: Full integration with Shipday API for order management
- **Webhook Support**: Handle Shipday webhooks for real-time order status updates

## API Endpoints

### AVS Integration
- `POST /api/v1/avs/webhook` - Receive address verification requests from AVS
- `POST /api/v1/avs/submit-verification` - Submit completed verification results to AVS
- `POST /api/v1/avs/validate-payload` - Validate AVS payload before submission

### Order Management
- `GET /api/v1/orders/<order_id>` - Get order details from Shipday
- `GET /api/v1/orders` - List completed orders

### Webhooks
- `POST /webhooks/shipday` - Handle Shipday webhook events

### Utility
- `GET /health` - Health check endpoint
- `GET /docs` - Swagger API documentation

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SHIPDAY_API_KEY` | Yes | Shipday API key |
| `AVS_VENDOR_ID` | Yes | AVS vendor ID (GUID) |
| `AVS_SUBSCRIPTION_KEY` | Yes | AVS subscription key |
| `AVS_BASE_URL` | No | AVS API base URL (default: development) |
| `API_KEY` | No | API key for authentication |
| `SHIPDAY_WEBHOOK_SECRET` | No | Webhook verification secret |
| `PORT` | No | Server port (default: 5000) |

## Deployment

The service is configured for deployment on Render. Push to the main branch to trigger automatic deployment.

## License

MIT License
