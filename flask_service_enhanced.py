"""
Enhanced Flask Service for AVS-Shipday Integration
Provides REST API endpoints for bidirectional AVS and Shipday integration.
"""

import os
import json
import logging
import hmac
from functools import wraps
from datetime import datetime
from typing import Optional, Dict, Any

from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from flasgger import Swagger

from avs_shipday_integration import (
    AVSShipdayIntegration,
    VerificationStatus,
    MediaType
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
CORS(app, origins=os.getenv("CORS_ORIGINS", "*").split(","))

# Configuration
app.config.update(
    JSON_SORT_KEYS=False,
    MAX_CONTENT_LENGTH=16 * 1024 * 1024  # 16MB max for photo uploads
)

# Swagger configuration
swagger_config = {
    "headers": [],
    "specs": [
        {
            "endpoint": "apispec",
            "route": "/apispec.json",
            "rule_filter": lambda rule: True,
            "model_filter": lambda tag: True,
        }
    ],
    "static_url_path": "/flasgger_static",
    "swagger_ui": True,
    "specs_route": "/docs"
}

swagger_template = {
    "swagger": "2.0",
    "info": {
        "title": "AVS-Shipday Integration API",
        "description": "REST API for bidirectional AVS and Shipday integration. Handles address verification requests from AVS and submits verification results back.",
        "version": "2.0.0",
        "contact": {
            "name": "API Support"
        }
    },
    "securityDefinitions": {
        "ApiKeyAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "API key for authentication"
        },
        "BearerAuth": {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "Bearer token (format: Bearer <api_key>)"
        }
    },
    "tags": [
        {"name": "Health", "description": "Health check endpoints"},
        {"name": "AVS Integration", "description": "AVS address verification endpoints"},
        {"name": "Orders", "description": "Shipday order management endpoints"},
        {"name": "Webhooks", "description": "Webhook handlers"}
    ]
}

swagger = Swagger(app, config=swagger_config, template=swagger_template)

# Environment variables
API_KEY = os.getenv("API_KEY")
SHIPDAY_API_KEY = os.getenv("SHIPDAY_API_KEY")
AVS_VENDOR_ID = os.getenv("AVS_VENDOR_ID")
AVS_SUBSCRIPTION_KEY = os.getenv("AVS_SUBSCRIPTION_KEY")
AVS_BASE_URL = os.getenv("AVS_BASE_URL", "https://alat-dev-apim.azure-api.net/customops")
SHIPDAY_WEBHOOK_SECRET = os.getenv("SHIPDAY_WEBHOOK_SECRET")


def require_api_key(f):
    """Decorator to require API key authentication (currently disabled)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Security temporarily disabled - all requests allowed through
        return f(*args, **kwargs)
    return decorated


def verify_shipday_webhook(f):
    """Decorator to verify Shipday webhook token from multiple headers."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not SHIPDAY_WEBHOOK_SECRET:
            logger.warning("Webhook secret not configured, skipping verification")
            return f(*args, **kwargs)

        # Check multiple headers for the webhook token
        webhook_token = request.headers.get("X-Webhook-Token", "")
        shipday_signature = request.headers.get("X-Shipday-Signature", "")
        auth_header = request.headers.get("Authorization", "")

        # Extract token from Authorization header (Bearer token or raw)
        auth_token = ""
        if auth_header.startswith("Bearer "):
            auth_token = auth_header[7:]
        else:
            auth_token = auth_header

        # Check if any of the provided tokens match the secret
        provided_tokens = [webhook_token, shipday_signature, auth_token]
        token_valid = any(
            token and hmac.compare_digest(token, SHIPDAY_WEBHOOK_SECRET)
            for token in provided_tokens
        )

        if not token_valid:
            logger.warning("Invalid webhook token received")
            return jsonify({"error": "Unauthorized", "message": "Invalid or missing webhook token"}), 401

        return f(*args, **kwargs)
    return decorated


@app.errorhandler(Exception)
def handle_exception(e):
    """Global exception handler."""
    if isinstance(e, HTTPException):
        return jsonify({"error": e.name, "message": e.description}), e.code

    logger.exception("Unhandled exception")
    return jsonify({
        "error": "Internal Server Error",
        "message": "An unexpected error occurred"
    }), 500


# Initialize integration lazily
_integration: Optional[AVSShipdayIntegration] = None


def get_integration() -> AVSShipdayIntegration:
    """Get or create the AVS-Shipday integration instance."""
    global _integration
    if _integration is None:
        if not SHIPDAY_API_KEY:
            raise ValueError("SHIPDAY_API_KEY environment variable is required")
        if not AVS_VENDOR_ID:
            raise ValueError("AVS_VENDOR_ID environment variable is required")
        if not AVS_SUBSCRIPTION_KEY:
            raise ValueError("AVS_SUBSCRIPTION_KEY environment variable is required")

        _integration = AVSShipdayIntegration(
            shipday_api_key=SHIPDAY_API_KEY,
            avs_vendor_id=AVS_VENDOR_ID,
            avs_subscription_key=AVS_SUBSCRIPTION_KEY,
            avs_base_url=AVS_BASE_URL
        )
    return _integration


# Health check endpoint
@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint for load balancers.
    ---
    tags:
      - Health
    responses:
      200:
        description: Service is healthy
        schema:
          type: object
          properties:
            status:
              type: string
              example: healthy
            timestamp:
              type: string
              example: "2025-01-25T10:00:00"
            service:
              type: string
              example: avs-shipday-integration
            version:
              type: string
              example: "2.0.0"
    """
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "avs-shipday-integration",
        "version": "2.0.0"
    })


@app.route("/", methods=["GET"])
def root():
    """Root endpoint with API information."""
    return jsonify({
        "service": "AVS-Shipday Integration API",
        "version": "2.0.0",
        "description": "Bidirectional integration between AVS and Shipday",
        "endpoints": {
            "health": "/health",
            "docs": "/docs",
            "avs_webhook": "/api/v1/avs/webhook",
            "submit_verification": "/api/v1/avs/submit-verification",
            "vendor_submit": "/vendor/address-verification/submit",
            "get_order": "/api/v1/orders/<order_id>",
            "list_orders": "/api/v1/orders",
            "shipday_webhook": "/webhooks/shipday"
        }
    })


# ==================== AVS INTEGRATION ENDPOINTS ====================

@app.route("/api/v1/avs/webhook", methods=["POST"])
@require_api_key
def avs_webhook():
    """Handle incoming address verification requests from AVS.
    ---
    tags:
      - AVS Integration
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - vendorId
            - addressVerificationResponses
          properties:
            vendorId:
              type: string
              description: Vendor ID (GUID)
              example: "12345678-1234-1234-1234-123456789012"
            addressVerificationResponses:
              type: array
              items:
                type: object
                properties:
                  activityId:
                    type: string
                    example: "ACT-001"
                  customerName:
                    type: string
                    example: "John Doe"
                  address:
                    type: string
                    example: "123 Main Street, Lagos, Nigeria"
                  visitDate:
                    type: string
                    example: "2025-01-28T10:00:00Z"
                  additionalComments:
                    type: string
    responses:
      200:
        description: Request processed successfully
        schema:
          type: object
          properties:
            status:
              type: string
              example: success
            message:
              type: string
            requestId:
              type: string
            createdTasks:
              type: array
              items:
                type: object
      400:
        description: Bad request - invalid payload
      401:
        description: Unauthorized - invalid or missing API key
      500:
        description: Server error
    """
    data = request.get_json()
    if not data:
        return jsonify({
            "status": "error",
            "message": "Request body is required",
            "requestId": None
        }), 400

    try:
        integration = get_integration()
        result = integration.handle_avs_request(data)

        status_code = 200 if result.get("status") == "success" else 400
        return jsonify(result), status_code

    except ValueError as e:
        return jsonify({
            "status": "error",
            "message": str(e),
            "requestId": None
        }), 500


@app.route("/api/v1/avs/submit-verification", methods=["POST"])
@require_api_key
def submit_verification():
    """Submit completed verification result back to AVS.
    ---
    tags:
      - AVS Integration
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - activityId
            - verificationDetails
          properties:
            activityId:
              type: string
              description: Original activity ID from AVS
              example: "ACT-001"
            shipdayOrderId:
              type: string
              description: Shipday order ID (optional, will fetch order data)
              example: "12345"
            verificationDetails:
              type: object
              required:
                - customerName
                - address
                - addressExists
              properties:
                customerName:
                  type: string
                  example: "John Doe"
                address:
                  type: string
                  example: "123 Main Street, Lagos, Nigeria"
                visitDate:
                  type: string
                  example: "2025-01-28T10:00:00Z"
                addressExists:
                  type: boolean
                  example: true
                isResidentialAddress:
                  type: boolean
                  example: true
                isCustomerResidence:
                  type: boolean
                  example: true
                isCustomerKnown:
                  type: boolean
                  example: false
                relationshipWithPersonMet:
                  type: string
                  example: "Self"
                nameOfPersonMet:
                  type: string
                  example: "John Doe"
                easeOfLocation:
                  type: string
                  enum: [Easy, Medium, Hard]
                  example: "Easy"
                comments:
                  type: string
                additionalComments:
                  type: string
                metOthers:
                  type: boolean
                  example: false
                verificationStatus:
                  type: integer
                  description: "1=PENDING, 2=SUCCESS, 3=FAILED, 4=RETURNED"
                  example: 2
                reportUrl:
                  type: string
                  example: "https://example.com/report/123"
                photos:
                  type: array
                  items:
                    type: object
                    properties:
                      fileName:
                        type: string
                      base64Content:
                        type: string
                      contentType:
                        type: string
                      caption:
                        type: string
                      timestamp:
                        type: string
                      latitude:
                        type: string
                      longitude:
                        type: string
    responses:
      200:
        description: Verification submitted successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            status_code:
              type: integer
            message:
              type: string
      400:
        description: Bad request - missing required fields
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    data = request.get_json()
    if not data:
        return jsonify({
            "success": False,
            "message": "Request body is required"
        }), 400

    activity_id = data.get("activityId")
    verification_details = data.get("verificationDetails", {})

    if not activity_id:
        return jsonify({
            "success": False,
            "message": "activityId is required"
        }), 400

    if not verification_details:
        return jsonify({
            "success": False,
            "message": "verificationDetails is required"
        }), 400

    try:
        integration = get_integration()

        # Get Shipday order data if order ID provided
        shipday_order_data = {}
        shipday_order_id = data.get("shipdayOrderId")
        if shipday_order_id:
            order_data = integration.get_shipday_order(shipday_order_id)
            if order_data:
                shipday_order_data = order_data

        # Submit to AVS
        result = integration.submit_verification_result(
            activity_id=activity_id,
            shipday_order_data=shipday_order_data,
            verification_details=verification_details
        )

        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code

    except ValueError as e:
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@app.route("/api/v1/avs/validate-payload", methods=["POST"])
@require_api_key
def validate_avs_payload():
    """Validate an AVS payload before submission.
    ---
    tags:
      - AVS Integration
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
    responses:
      200:
        description: Validation result
        schema:
          type: object
          properties:
            valid:
              type: boolean
            errors:
              type: array
              items:
                type: string
      400:
        description: Bad request
      401:
        description: Unauthorized
    """
    data = request.get_json()
    if not data:
        return jsonify({
            "valid": False,
            "errors": ["Request body is required"]
        }), 400

    is_valid, errors = AVSShipdayIntegration.validate_avs_payload(data)

    return jsonify({
        "valid": is_valid,
        "errors": errors
    })


# ==================== VENDOR ENDPOINT (Legacy) ====================

@app.route("/vendor/address-verification/submit", methods=["POST"])
@require_api_key
def vendor_address_verification_submit():
    """Submit address verification response (Legacy endpoint).

    This endpoint receives completed verification results and submits them to AVS.
    ---
    tags:
      - AVS Integration
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required:
            - vendorId
            - addressVerificationResponses
          properties:
            vendorId:
              type: string
              description: Vendor ID (GUID)
              example: "9773FC2D-8DC2-40E6-B272-71AC04719FBD"
            addressVerificationResponses:
              type: array
              items:
                type: object
                required:
                  - activityId
                  - customerName
                  - address
                  - visitDate
                  - addressExists
                  - isResidentialAddress
                  - isCustomerResidence
                  - isCustomerKnown
                  - relationshipWithPersonMet
                  - nameOfPersonMet
                  - easeOfLocation
                  - receivedDate
                  - metOthers
                  - verificationStatus
                  - addressMedia
                  - reportUrl
                properties:
                  activityId:
                    type: string
                  customerName:
                    type: string
                  address:
                    type: string
                  visitDate:
                    type: string
                  addressExists:
                    type: boolean
                  isResidentialAddress:
                    type: boolean
                  isCustomerResidence:
                    type: boolean
                  isCustomerKnown:
                    type: boolean
                  relationshipWithPersonMet:
                    type: string
                  nameOfPersonMet:
                    type: string
                  easeOfLocation:
                    type: string
                  comments:
                    type: string
                  additionalComments:
                    type: string
                  receivedDate:
                    type: string
                  metOthers:
                    type: boolean
                  verificationStatus:
                    type: integer
                    description: "1=PENDING, 2=SUCCESS, 3=FAILED, 4=RETURNED"
                  addressMedia:
                    type: array
                    items:
                      type: object
                  reportUrl:
                    type: string
    responses:
      200:
        description: Verification submitted successfully
        schema:
          type: object
          properties:
            success:
              type: boolean
            status_code:
              type: integer
            message:
              type: string
      400:
        description: Bad request - missing required fields or validation failed
      401:
        description: Unauthorized
      500:
        description: Server error
    """
    data = request.get_json()
    if not data:
        return jsonify({
            "success": False,
            "status_code": 400,
            "message": "Request body is required"
        }), 400

    vendor_id = data.get("vendorId")
    verification_responses = data.get("addressVerificationResponses", [])

    if not vendor_id:
        return jsonify({
            "success": False,
            "status_code": 400,
            "message": "vendorId is required"
        }), 400

    if not verification_responses:
        return jsonify({
            "success": False,
            "status_code": 400,
            "message": "addressVerificationResponses is required and must not be empty"
        }), 400

    # Validate the payload
    is_valid, errors = AVSShipdayIntegration.validate_avs_payload(data)
    if not is_valid:
        return jsonify({
            "success": False,
            "status_code": 400,
            "message": "Validation failed",
            "errors": errors
        }), 400

    try:
        integration = get_integration()

        # Process each verification response
        results = []
        for response in verification_responses:
            activity_id = response.get("activityId")

            # Build verification details from the response
            verification_details = {
                "customerName": response.get("customerName", ""),
                "address": response.get("address", ""),
                "visitDate": response.get("visitDate", ""),
                "addressExists": response.get("addressExists", True),
                "isResidentialAddress": response.get("isResidentialAddress", True),
                "isCustomerResidence": response.get("isCustomerResidence", True),
                "isCustomerKnown": response.get("isCustomerKnown", False),
                "relationshipWithPersonMet": response.get("relationshipWithPersonMet", ""),
                "nameOfPersonMet": response.get("nameOfPersonMet", ""),
                "easeOfLocation": response.get("easeOfLocation", "Medium"),
                "comments": response.get("comments", ""),
                "additionalComments": response.get("additionalComments", ""),
                "metOthers": response.get("metOthers", False),
                "verificationStatus": response.get("verificationStatus", VerificationStatus.SUCCESS.value),
                "reportUrl": response.get("reportUrl", ""),
                "photos": []  # Convert addressMedia to photos format if needed
            }

            # Convert addressMedia to photos format
            address_media = response.get("addressMedia", [])
            for media in address_media:
                verification_details["photos"].append({
                    "fileName": media.get("fileName", "photo.jpg"),
                    "base64Content": media.get("contentBase64", ""),
                    "contentType": media.get("contentType", "image/jpeg"),
                    "caption": media.get("caption", ""),
                    "timestamp": media.get("takenAt", ""),
                    "latitude": media.get("latitude", ""),
                    "longitude": media.get("longitude", "")
                })

            # Submit to AVS
            result = integration.submit_verification_result(
                activity_id=activity_id,
                shipday_order_data={},
                verification_details=verification_details
            )
            results.append({
                "activityId": activity_id,
                "result": result
            })

        # Check if all submissions were successful
        all_success = all(r["result"].get("success", False) for r in results)

        return jsonify({
            "success": all_success,
            "status_code": 200 if all_success else 207,
            "message": "All verifications submitted successfully" if all_success else "Some verifications failed",
            "results": results
        }), 200 if all_success else 207

    except ValueError as e:
        return jsonify({
            "success": False,
            "status_code": 500,
            "message": str(e)
        }), 500


# ==================== ORDER MANAGEMENT ENDPOINTS ====================

@app.route("/api/v1/orders/<order_id>", methods=["GET"])
@require_api_key
def get_order(order_id: str):
    """Get order details from Shipday.
    ---
    tags:
      - Orders
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    parameters:
      - in: path
        name: order_id
        type: string
        required: true
        description: The Shipday order ID
    responses:
      200:
        description: Order details
      401:
        description: Unauthorized
      404:
        description: Order not found
      500:
        description: Configuration error
    """
    try:
        integration = get_integration()
        order_data = integration.get_shipday_order(order_id)

        if order_data:
            return jsonify(order_data)
        else:
            return jsonify({"error": "Not Found", "message": "Order not found"}), 404

    except ValueError as e:
        return jsonify({"error": "Configuration Error", "message": str(e)}), 500


@app.route("/api/v1/orders", methods=["GET"])
@require_api_key
def list_orders():
    """List completed orders from Shipday.
    ---
    tags:
      - Orders
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    parameters:
      - in: query
        name: start_date
        type: string
        description: Start date in ISO format
      - in: query
        name: end_date
        type: string
        description: End date in ISO format
    responses:
      200:
        description: List of completed orders
      401:
        description: Unauthorized
      500:
        description: Configuration error
    """
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    try:
        integration = get_integration()
        orders = integration.list_completed_orders(start_date, end_date)

        return jsonify({
            "success": True,
            "count": len(orders),
            "orders": orders
        })

    except ValueError as e:
        return jsonify({"error": "Configuration Error", "message": str(e)}), 500


# ==================== SHIPDAY WEBHOOK HANDLER ====================

@app.route("/webhooks/shipday", methods=["POST"])
@verify_shipday_webhook
def shipday_webhook():
    """Handle Shipday webhook events.
    ---
    tags:
      - Webhooks
    parameters:
      - in: header
        name: X-Webhook-Token
        type: string
        description: Webhook verification token
      - in: header
        name: X-Shipday-Signature
        type: string
        description: Alternative Shipday signature header
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            event:
              type: string
              enum: [order.status.changed, order.assigned, order.picked_up, order.delivered, carrier.location.updated]
              example: "order.status.changed"
            orderId:
              type: string
              example: "12345"
            newStatus:
              type: string
            oldStatus:
              type: string
            carrier:
              type: object
            timestamp:
              type: string
    responses:
      200:
        description: Webhook processed successfully
      400:
        description: Bad request - no payload
      401:
        description: Unauthorized - invalid webhook token
      500:
        description: Processing error
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "Bad Request", "message": "No payload"}), 400

    event_type = data.get("event")
    logger.info(f"Received Shipday webhook: {event_type}")

    # Process different event types
    handlers = {
        "order.status.changed": handle_status_change,
        "order.assigned": handle_order_assigned,
        "order.picked_up": handle_order_picked_up,
        "order.delivered": handle_order_delivered,
        "carrier.location.updated": handle_carrier_location
    }

    handler = handlers.get(event_type)
    if handler:
        try:
            result = handler(data)
            return jsonify({"success": True, "result": result})
        except Exception as e:
            logger.exception(f"Error handling webhook {event_type}")
            return jsonify({"error": "Processing Error", "message": str(e)}), 500
    else:
        logger.warning(f"Unhandled webhook event: {event_type}")
        return jsonify({"success": True, "message": "Event acknowledged but not processed"})


def handle_status_change(data: Dict) -> Dict:
    """Handle order status change event."""
    order_id = data.get("orderId")
    new_status = data.get("newStatus")
    old_status = data.get("oldStatus")

    logger.info(f"Order {order_id} status changed: {old_status} -> {new_status}")

    # If order is delivered, we might want to auto-submit verification to AVS
    if new_status == "DELIVERED":
        logger.info(f"Order {order_id} delivered - verification can be submitted to AVS")

    return {"order_id": order_id, "new_status": new_status}


def handle_order_assigned(data: Dict) -> Dict:
    """Handle order assigned to carrier event."""
    order_id = data.get("orderId")
    carrier = data.get("carrier", {})

    logger.info(f"Order {order_id} assigned to carrier {carrier.get('name')}")

    return {"order_id": order_id, "carrier": carrier.get("name")}


def handle_order_picked_up(data: Dict) -> Dict:
    """Handle order picked up event."""
    order_id = data.get("orderId")
    pickup_time = data.get("timestamp")

    logger.info(f"Order {order_id} picked up at {pickup_time}")

    return {"order_id": order_id, "pickup_time": pickup_time}


def handle_order_delivered(data: Dict) -> Dict:
    """Handle order delivered event."""
    order_id = data.get("orderId")
    delivery_time = data.get("timestamp")
    proof_photos = data.get("proofPhotos", [])

    logger.info(f"Order {order_id} delivered at {delivery_time} with {len(proof_photos)} photos")

    return {
        "order_id": order_id,
        "delivery_time": delivery_time,
        "photo_count": len(proof_photos)
    }


def handle_carrier_location(data: Dict) -> Dict:
    """Handle carrier location update event."""
    carrier_id = data.get("carrierId")
    location = data.get("location", {})

    logger.debug(f"Carrier {carrier_id} location: {location}")

    return {"carrier_id": carrier_id, "location": location}


# ==================== UTILITY ENDPOINTS ====================

@app.route("/api/v1/debug/config", methods=["GET"])
@require_api_key
def debug_config():
    """Debug endpoint to check AVS configuration (remove in production).
    ---
    tags:
      - AVS Integration
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    responses:
      200:
        description: Current configuration
    """
    return jsonify({
        "api_key_set": bool(API_KEY),
        "avs_base_url": AVS_BASE_URL,
        "avs_vendor_id_set": bool(AVS_VENDOR_ID),
        "avs_vendor_id_preview": AVS_VENDOR_ID[:8] + "..." if AVS_VENDOR_ID and len(AVS_VENDOR_ID) > 8 else "not set",
        "avs_subscription_key_set": bool(AVS_SUBSCRIPTION_KEY),
        "avs_subscription_key_preview": AVS_SUBSCRIPTION_KEY[:8] + "..." if AVS_SUBSCRIPTION_KEY and len(AVS_SUBSCRIPTION_KEY) > 8 else "not set",
        "shipday_api_key_set": bool(SHIPDAY_API_KEY),
        "full_avs_endpoint": f"{AVS_BASE_URL}/api/AddressVendor/receive-verification-response"
    })


@app.route("/api/v1/debug/test-shipday", methods=["POST"])
@require_api_key
def test_shipday():
    """Test Shipday order creation directly.
    ---
    tags:
      - AVS Integration
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    responses:
      200:
        description: Shipday test result
    """
    import requests as req

    try:
        # Test directly with Shipday API
        shipday_api_key = SHIPDAY_API_KEY
        shipday_url = "https://api.shipday.com/orders"

        # Shipday API requires restaurant name for pickup location
        test_payload = {
            "orderNumber": "AVS-TEST-" + datetime.utcnow().strftime("%Y%m%d%H%M%S"),
            "customerName": "Test Customer",
            "customerAddress": "123 Test Street, Lagos, Nigeria",
            "customerPhoneNumber": "08012345678",
            "restaurantName": "AVS Verification HQ",
            "restaurantAddress": "Victoria Island, Lagos, Nigeria",
            "restaurantPhoneNumber": "08000000000"
        }

        # Try multiple auth methods
        results = {}

        # Method 1: Basic auth header
        headers1 = {
            "Authorization": f"Basic {shipday_api_key}",
            "Content-Type": "application/json"
        }
        resp1 = req.post(shipday_url, headers=headers1, json=test_payload, timeout=30)
        results["basic_auth"] = {"status": resp1.status_code, "body": resp1.text[:300]}

        # Method 2: Bearer auth header
        headers2 = {
            "Authorization": f"Bearer {shipday_api_key}",
            "Content-Type": "application/json"
        }
        resp2 = req.post(shipday_url, headers=headers2, json=test_payload, timeout=30)
        results["bearer_auth"] = {"status": resp2.status_code, "body": resp2.text[:300]}

        # Method 3: API key in header
        headers3 = {
            "X-Api-Key": shipday_api_key,
            "Content-Type": "application/json"
        }
        resp3 = req.post(shipday_url, headers=headers3, json=test_payload, timeout=30)
        results["x_api_key"] = {"status": resp3.status_code, "body": resp3.text[:300]}

        return jsonify({
            "shipday_url": shipday_url,
            "shipday_api_key_preview": shipday_api_key[:10] + "..." if shipday_api_key else "NOT SET",
            "results": results
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/api/v1/debug/test-avs-webhook", methods=["POST"])
@require_api_key
def test_avs_webhook():
    """Test AVS webhook with detailed error reporting."""
    try:
        integration = get_integration()

        test_data = {
            "vendorId": "C165A237-6AD4-4145-9F07-B1A06A60F7D1",
            "addressVerificationResponses": [{
                "activityId": "DEBUG-TEST-" + datetime.utcnow().strftime("%Y%m%d%H%M%S"),
                "customerName": "Debug Test Customer",
                "address": "123 Debug Street, Lagos, Nigeria",
                "visitDate": "2026-01-29T10:00:00Z",
                "additionalComments": "Debug test"
            }]
        }

        # Call the integration method directly
        result = integration.handle_avs_request(test_data)

        return jsonify({
            "test_data": test_data,
            "result": result,
            "integration_shipday_key_preview": integration.shipday_api_key[:10] + "..." if integration.shipday_api_key else "NOT SET"
        })
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@app.route("/api/v1/verification-statuses", methods=["GET"])
def get_verification_statuses():
    """Get list of valid verification statuses.
    ---
    tags:
      - AVS Integration
    responses:
      200:
        description: List of verification statuses
    """
    return jsonify({
        "statuses": [
            {"value": status.value, "name": status.name}
            for status in VerificationStatus
        ]
    })


@app.route("/api/v1/media-types", methods=["GET"])
def get_media_types():
    """Get list of valid media types.
    ---
    tags:
      - AVS Integration
    responses:
      200:
        description: List of media types
    """
    return jsonify({
        "mediaTypes": [
            {"value": media_type.value, "name": media_type.name}
            for media_type in MediaType
        ]
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"

    logger.info(f"Starting AVS-Shipday Integration Service v2.0.0 on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
