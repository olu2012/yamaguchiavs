"""
Enhanced Flask Service for AVS-Shipday Integration
Provides REST API endpoints for bidirectional AVS and Shipday integration.
"""

import os
import json
import logging
import hmac
import threading
import time
from functools import wraps
from datetime import datetime
from typing import Optional, Dict, Any

from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from flasgger import Swagger

import order_cache
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
    """Decorator to require API key authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not API_KEY:
            # API key not configured - reject all requests
            logger.error("API_KEY environment variable is not set. All authenticated requests will be rejected.")
            return jsonify({"error": "Service Misconfigured", "message": "API authentication is not configured"}), 503

        auth_header = request.headers.get("Authorization", "")
        api_key_header = request.headers.get("X-API-Key", "")

        # Check Bearer token or X-API-Key header
        if auth_header.startswith("Bearer "):
            provided_key = auth_header[7:]
        else:
            provided_key = api_key_header

        if not provided_key or provided_key != API_KEY:
            return jsonify({"error": "Unauthorized", "message": "Invalid or missing API key"}), 401

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
        shipday_token = request.headers.get("Token", "")
        shipday_signature = request.headers.get("X-Shipday-Signature", "")
        auth_header = request.headers.get("Authorization", "")

        # Extract token from Authorization header (Bearer token or raw)
        auth_token = ""
        if auth_header.startswith("Bearer "):
            auth_token = auth_header[7:]
        else:
            auth_token = auth_header

        # Check if any of the provided tokens match the secret
        provided_tokens = [webhook_token, shipday_token, shipday_signature, auth_token]
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
    """Submit address verification request or response.

    Accepts two payload formats:
    1. New AVS format (has customer_reference) - creates a Shipday delivery order.
    2. Legacy format (has vendorId) - submits completed verification results to AVS.
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
          properties:
            customer_reference:
              type: string
              description: "New AVS format: unique reference for this verification request"
              example: "CUST-20260202-001"
            verification_type:
              type: string
              description: "New AVS format: type of verification"
              example: "AddressVerification"
            subject_first_name:
              type: string
              example: "Test1"
            subject_middle_name:
              type: string
              example: "Tester3"
            subject_last_name:
              type: string
              example: "Tester"
            subject_email:
              type: string
              example: "test@example.com"
            subject_phone:
              type: string
              example: "+2348012345678"
            subject_date_of_birth:
              type: string
              example: "1992-07-15"
            address_street:
              type: string
              example: "No. 12 Peace Avenue"
            address_city:
              type: string
              example: "Ikeja"
            address_state:
              type: string
              example: "Lagos"
            address_lga:
              type: string
              example: "Ikeja"
            address_landmark:
              type: string
              example: "Near Ikeja City Mall"
            address_postal_code:
              type: string
              example: "100271"
            address_country:
              type: string
              example: "Nigeria"
            fullAddress:
              type: string
              description: "New AVS format: pre-formatted complete address string. When provided, takes precedence over the individual address_* fields."
              example: "45 Aminu Kano Crescent, Wuse II, Abuja, FCT 900288, Nigeria"
            additionalInformation:
              type: string
              description: "New AVS format: extra notes or landmarks to assist the field agent (e.g. house colour, gate description)."
              example: "House with Green Color"
            vendorId:
              type: string
              description: "Legacy format: Vendor ID (GUID)"
              example: "9773FC2D-8DC2-40E6-B272-71AC04719FBD"
            addressVerificationResponses:
              type: array
              description: "Legacy format: completed verification results"
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
                  - comments
                  - receivedDate
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
                  receivedDate:
                    type: string
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

    try:
        integration = get_integration()

        # Detect payload format - new AVS format has customer_reference
        if "customer_reference" in data:
            # New AVS format - create Shipday order
            result = integration.handle_avs_request(data)

            if result.get("status") == "success":
                return jsonify({
                    "success": True,
                    "status_code": 200,
                    "message": result.get("message"),
                    "reference": result.get("requestId"),
                    "createdTasks": result.get("createdTasks", [])
                }), 200
            else:
                return jsonify({
                    "success": False,
                    "status_code": 400,
                    "message": result.get("message")
                }), 400

        # Legacy format - requires vendorId and addressVerificationResponses
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
                "verificationStatus": response.get("verificationStatus", VerificationStatus.SUCCESS.value),
                "reportUrl": response.get("reportUrl", ""),
                "photos": []
            }

            # Convert addressMedia to photos format
            address_media = response.get("addressMedia", [])
            for media in address_media:
                verification_details["photos"].append({
                    "fileName": media.get("fileName", "photo.jpg"),
                    "base64Content": media.get("contentBase64", ""),
                    "contentType": media.get("contentType", "image/jpeg"),
                    "latitude": float(media.get("latitude", 0) or 0),
                    "longitude": float(media.get("longitude", 0) or 0)
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

    # Process different event types (Shipday uses uppercase event names)
    handlers = {
        "ORDER_INSERTED": handle_order_inserted,
        "ORDER_ASSIGNED": handle_order_assigned,
        "ORDER_PICKED_UP": handle_order_picked_up,
        "ORDER_DELIVERED": handle_order_delivered,
        "ORDER_COMPLETED": handle_order_completed,
        "ORDER_FAILED": handle_order_failed,
        "ORDER_INCOMPLETE": handle_order_incomplete,
        "ORDER_DELETE": handle_order_delete,
        "ORDER_STATUS_CHANGED": handle_status_change,
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


def handle_order_inserted(data: Dict) -> Dict:
    """Handle order inserted event - cache order_id → order_number for future DELETE lookups."""
    logger.info(f"ORDER_INSERTED raw payload: {json.dumps(data)}")
    order = data.get("order", {})
    order_id = order.get("id")
    order_number = order.get("order_number")

    if order_id and order_number:
        order_cache.store(str(order_id), str(order_number))
        logger.info(f"ORDER_INSERTED: cached {order_id} → {order_number}")
    else:
        logger.warning(f"ORDER_INSERTED: missing order_id={order_id} or order_number={order_number}, cannot cache")

    return {"order_id": order_id, "order_number": order_number}


def handle_status_change(data: Dict) -> Dict:
    """Handle order status change event."""
    order = data.get("order", {})
    order_id = order.get("id")
    order_number = order.get("order_number")
    order_status = data.get("order_status")

    logger.info(f"Order {order_id} ({order_number}) status changed to: {order_status}")

    return {"order_id": order_id, "order_number": order_number, "status": order_status}


def handle_order_assigned(data: Dict) -> Dict:
    """Handle order assigned to carrier event."""
    order = data.get("order", {})
    order_id = order.get("id")
    order_number = order.get("order_number")
    delivery = data.get("delivery_details", {})

    logger.info(f"Order {order_id} ({order_number}) assigned - delivering to {delivery.get('name')}")

    return {"order_id": order_id, "order_number": order_number, "delivery_to": delivery.get("name")}


def handle_order_picked_up(data: Dict) -> Dict:
    """Handle order picked up event."""
    order = data.get("order", {})
    order_id = order.get("id")
    order_number = order.get("order_number")
    timestamp = data.get("timestamp")
    pickup = data.get("pickup_details", {})

    logger.info(f"Order {order_id} ({order_number}) picked up from {pickup.get('name')}")

    return {"order_id": order_id, "order_number": order_number, "pickup_time": timestamp}


def _extract_pod_photos_from_shipday_order(order_data, label="Phase 2"):
    """Extract and download POD photos from a Shipday API order response.
    Shipday returns photos in proofOfDelivery.imageUrls (and optionally signaturePath)."""
    photos = []
    proof = order_data.get("proofOfDelivery") or {}
    image_urls = proof.get("imageUrls") or []
    signature_url = proof.get("signaturePath")

    all_urls = list(image_urls)
    if signature_url and isinstance(signature_url, str) and signature_url.startswith("http"):
        all_urls.append(signature_url)

    logger.info(f"[{label}] Found {len(image_urls)} POD image(s) + {'1 signature' if signature_url else 'no signature'}")
    for idx, url in enumerate(all_urls):
        if isinstance(url, str) and url.startswith("http"):
            base64_content, content_type = AVSShipdayIntegration.download_image_to_base64(url)
            if base64_content:
                fname = "signature.jpg" if idx >= len(image_urls) else f"pod_{idx + 1}.jpg"
                photos.append({
                    "fileName": fname,
                    "base64Content": base64_content,
                    "contentType": content_type,
                    "latitude": float(proof.get("latitude") or 0),
                    "longitude": float(proof.get("longitude") or 0)
                })
    return photos


def _process_and_submit_to_avs(order_id, activity_id, delivery, visit_date,
                                tracking_url, comments, verification_status,
                                delay_seconds=0):
    """Fetch fresh POD photos from Shipday API then submit to AVS.
    Designed to run in a background thread. Waits delay_seconds before fetching."""
    if delay_seconds > 0:
        logger.info(f"[Phase 2] Waiting {delay_seconds}s for POD photos to upload for order {order_id}")
        time.sleep(delay_seconds)

    try:
        integration = get_integration()

        # Fetch fresh order data using order_number (UUID = activity_id) — Shipday returns
        # 404 for numeric IDs on completed orders, but UUID order numbers work fine.
        photos = []
        raw = integration.get_shipday_order(str(activity_id))
        if raw:
            # Shipday returns a list when queried by order number
            fresh_order = raw[0] if isinstance(raw, list) and raw else raw
            photos = _extract_pod_photos_from_shipday_order(fresh_order)
            logger.info(f"[Phase 2] Fetched order {order_id} from Shipday API — {len(photos)} photo(s) downloaded")
        else:
            logger.warning(f"[Phase 2] Could not fetch order {order_id} from Shipday API — proceeding without photos")

        verification_details = {
            "customerName": delivery.get("name", ""),
            "address": delivery.get("address", ""),
            "visitDate": visit_date,
            "verificationStatus": verification_status,
            "comments": comments,
            "reportUrl": tracking_url or "",
            "addressExists": True,
            "isResidentialAddress": True,
            "isCustomerResidence": True,
            "isCustomerKnown": verification_status == VerificationStatus.SUCCESS.value,
            "relationshipWithPersonMet": "Self",
            "nameOfPersonMet": delivery.get("name", "Not specified"),
            "easeOfLocation": "Medium",
            "photos": photos
        }

        logger.info(f"[Phase 2] Submitting to AVS for activityId: {activity_id} with {len(photos)} photo(s)")
        avs_result = integration.submit_verification_result(
            activity_id=activity_id,
            shipday_order_data={},
            verification_details=verification_details
        )
        logger.info(f"[Phase 2] AVS submission result: success={avs_result.get('success')}, status={avs_result.get('status_code')}")
        return avs_result

    except Exception as e:
        logger.error(f"[Phase 2] Error in deferred AVS submission for order {order_id}: {e}")
        return {"success": False, "error": str(e)}


def handle_order_delivered(data: Dict) -> Dict:
    """Handle order delivered event - auto-submit verification result to AVS."""
    order = data.get("order", {})
    order_id = order.get("id")
    order_number = order.get("order_number")
    timestamp = data.get("timestamp")
    delivery = data.get("delivery_details", {})
    delivery_note = data.get("delivery_note", "")
    tracking_url = data.get("trackingUrl", "")

    logger.info(f"Order {order_id} ({order_number}) DELIVERED to {delivery.get('name')} — will fetch PODs in 60s")

    activity_id = order_number
    if not activity_id:
        logger.error(f"Order {order_id} has no order_number, cannot submit to AVS")
        return {"order_id": order_id, "error": "No order_number/activityId"}

    visit_date = datetime.utcnow().isoformat() + 'Z'
    if timestamp:
        try:
            visit_date = datetime.utcfromtimestamp(int(timestamp) / 1000).isoformat() + 'Z'
        except (ValueError, TypeError, OSError):
            pass

    threading.Thread(
        target=_process_and_submit_to_avs,
        args=(order_id, activity_id, delivery, visit_date, tracking_url,
              delivery_note or "Verification completed - order delivered successfully",
              VerificationStatus.SUCCESS.value, 60),
        daemon=True
    ).start()

    return {
        "order_id": order_id,
        "order_number": order_number,
        "delivery_time": timestamp,
        "delivery_address": delivery.get("address"),
        "avs_submission": "deferred_60s"
    }


def handle_order_failed(data: Dict) -> Dict:
    """Handle order failed event - auto-submit failed verification to AVS."""
    order = data.get("order", {})
    order_id = order.get("id")
    order_number = order.get("order_number")
    order_status = data.get("order_status")
    delivery = data.get("delivery_details", {})
    delivery_note = data.get("delivery_note", "")
    timestamp = data.get("timestamp")
    tracking_url = data.get("trackingUrl", "")
    pods = data.get("pods", [])

    logger.warning(f"Order {order_id} ({order_number}) FAILED - status: {order_status}, delivery to: {delivery.get('name')}, note: {delivery_note}")

    # order_number IS the activityId
    activity_id = order_number

    if not activity_id:
        logger.error(f"Order {order_id} has no order_number, cannot submit to AVS")
        return {"order_id": order_id, "error": "No order_number/activityId"}

    # Convert timestamp (epoch ms) to ISO format
    visit_date = datetime.utcnow().isoformat() + 'Z'
    if timestamp:
        try:
            visit_date = datetime.utcfromtimestamp(int(timestamp) / 1000).isoformat() + 'Z'
        except (ValueError, TypeError, OSError):
            pass

    # Process any proof-of-delivery photos (may have none for failed orders)
    photos = []
    try:
        integration = get_integration()
        for idx, pod_url in enumerate(pods):
            if isinstance(pod_url, str) and pod_url.startswith("http"):
                base64_content, content_type = AVSShipdayIntegration.download_image_to_base64(pod_url)
                if base64_content:
                    photos.append({
                        "fileName": f"pod_{idx + 1}.jpg",
                        "base64Content": base64_content,
                        "contentType": content_type,
                        "latitude": 0.0,
                        "longitude": 0.0
                    })
    except Exception as e:
        logger.error(f"Error processing POD photos: {e}")
        integration = get_integration()

    # Build failure comments
    failure_reason = delivery_note or order_status or "Order failed"
    comments = f"Verification failed - {failure_reason}"

    # Build verification details
    verification_details = {
        "customerName": delivery.get("name", ""),
        "address": delivery.get("address", ""),
        "visitDate": visit_date,
        "verificationStatus": VerificationStatus.FAILED.value,
        "comments": comments,
        "reportUrl": tracking_url or "",
        "addressExists": False,
        "isResidentialAddress": False,
        "isCustomerResidence": False,
        "isCustomerKnown": False,
        "relationshipWithPersonMet": "Not applicable",
        "nameOfPersonMet": "Not applicable",
        "easeOfLocation": "Hard",
        "photos": photos
    }

    # Submit to AVS
    logger.info(f"[Phase 2] Submitting failed result to AVS for activityId: {activity_id}")
    avs_result = integration.submit_verification_result(
        activity_id=activity_id,
        shipday_order_data={},
        verification_details=verification_details
    )
    logger.info(f"[Phase 2] AVS submission result: success={avs_result.get('success')}, status={avs_result.get('status_code')}")

    return {
        "order_id": order_id,
        "order_number": order_number,
        "status": order_status,
        "delivery_name": delivery.get("name"),
        "delivery_note": delivery_note,
        "avs_submitted": avs_result.get("success", False),
        "avs_status_code": avs_result.get("status_code")
    }


def handle_order_completed(data: Dict) -> Dict:
    """Handle order completed event - auto-submit successful verification to AVS."""
    order = data.get("order", {})
    order_id = order.get("id")
    order_number = order.get("order_number")
    timestamp = data.get("timestamp")
    delivery = data.get("delivery_details", {})
    delivery_note = data.get("delivery_note", "")
    tracking_url = data.get("trackingUrl", "")

    logger.info(f"Order {order_id} ({order_number}) COMPLETED - delivering to {delivery.get('name')} — will fetch PODs in 60s")

    activity_id = order_number
    if not activity_id:
        logger.error(f"Order {order_id} has no order_number, cannot submit to AVS")
        return {"order_id": order_id, "error": "No order_number/activityId"}

    visit_date = datetime.utcnow().isoformat() + 'Z'
    if timestamp:
        try:
            visit_date = datetime.utcfromtimestamp(int(timestamp) / 1000).isoformat() + 'Z'
        except (ValueError, TypeError, OSError):
            pass

    threading.Thread(
        target=_process_and_submit_to_avs,
        args=(order_id, activity_id, delivery, visit_date, tracking_url,
              delivery_note or "Verification completed - order completed successfully",
              VerificationStatus.SUCCESS.value, 60),
        daemon=True
    ).start()

    return {
        "order_id": order_id,
        "order_number": order_number,
        "delivery_time": timestamp,
        "delivery_address": delivery.get("address"),
        "avs_submission": "deferred_60s"
    }


def handle_order_incomplete(data: Dict) -> Dict:
    """Handle order incomplete event - auto-submit failed verification to AVS."""
    order = data.get("order", {})
    order_id = order.get("id")
    order_number = order.get("order_number")
    order_status = data.get("order_status")
    delivery = data.get("delivery_details", {})
    delivery_note = data.get("delivery_note", "")
    timestamp = data.get("timestamp")
    tracking_url = data.get("trackingUrl", "")
    pods = data.get("pods", [])

    logger.warning(f"Order {order_id} ({order_number}) INCOMPLETE - status: {order_status}, delivery to: {delivery.get('name')}, note: {delivery_note}")

    # order_number IS the activityId
    activity_id = order_number

    if not activity_id:
        logger.error(f"Order {order_id} has no order_number, cannot submit to AVS")
        return {"order_id": order_id, "error": "No order_number/activityId"}

    # Convert timestamp (epoch ms) to ISO format
    visit_date = datetime.utcnow().isoformat() + 'Z'
    if timestamp:
        try:
            visit_date = datetime.utcfromtimestamp(int(timestamp) / 1000).isoformat() + 'Z'
        except (ValueError, TypeError, OSError):
            pass

    # Process any proof-of-delivery photos (may have none for incomplete orders)
    photos = []
    try:
        integration = get_integration()
        for idx, pod_url in enumerate(pods):
            if isinstance(pod_url, str) and pod_url.startswith("http"):
                base64_content, content_type = AVSShipdayIntegration.download_image_to_base64(pod_url)
                if base64_content:
                    photos.append({
                        "fileName": f"pod_{idx + 1}.jpg",
                        "base64Content": base64_content,
                        "contentType": content_type,
                        "latitude": 0.0,
                        "longitude": 0.0
                    })
    except Exception as e:
        logger.error(f"Error processing POD photos: {e}")
        integration = get_integration()

    # Build failure comments
    failure_reason = delivery_note or order_status or "Order incomplete"
    comments = f"Verification incomplete - {failure_reason}"

    # Build verification details - incomplete = failed verification
    verification_details = {
        "customerName": delivery.get("name", ""),
        "address": delivery.get("address", ""),
        "visitDate": visit_date,
        "verificationStatus": VerificationStatus.FAILED.value,
        "comments": comments,
        "reportUrl": tracking_url or "",
        "addressExists": False,
        "isResidentialAddress": False,
        "isCustomerResidence": False,
        "isCustomerKnown": False,
        "relationshipWithPersonMet": "Not applicable",
        "nameOfPersonMet": "Not applicable",
        "easeOfLocation": "Hard",
        "photos": photos
    }

    # Submit to AVS
    logger.info(f"[Phase 2] Submitting incomplete result to AVS for activityId: {activity_id}")
    avs_result = integration.submit_verification_result(
        activity_id=activity_id,
        shipday_order_data={},
        verification_details=verification_details
    )
    logger.info(f"[Phase 2] AVS submission result: success={avs_result.get('success')}, status={avs_result.get('status_code')}")

    return {
        "order_id": order_id,
        "order_number": order_number,
        "status": order_status,
        "delivery_name": delivery.get("name"),
        "delivery_note": delivery_note,
        "avs_submitted": avs_result.get("success", False),
        "avs_status_code": avs_result.get("status_code")
    }


def handle_order_delete(data: Dict) -> Dict:
    """Handle order deleted event - submit returned verification to AVS."""
    logger.info(f"ORDER_DELETE raw payload: {json.dumps(data)}")

    # ORDER_DELETE payload differs from other events: order_id is top-level,
    # there is no nested "order" object, and no delivery_details.
    order_id = data.get("order_id")
    order_status = data.get("order_status")
    delivery_note = data.get("delivery_note", "")
    timestamp = data.get("timestamp")
    pods = data.get("pods", [])

    logger.warning(f"Order {order_id} DELETED - status: {order_status}, note: {delivery_note}")

    # ORDER_DELETE does not include order_number (activityId).
    # 1. Check local persistent cache (order_cache.py / SQLite on /data)
    # 2. Fall back to Shipday API (may already be purged)
    order_number = None
    delivery = {}
    tracking_url = ""
    integration = get_integration()

    # --- cache lookup ---
    cached_activity_id = order_cache.lookup(str(order_id))
    if cached_activity_id:
        order_number = cached_activity_id
        logger.info(f"Order {order_id}: resolved activityId={order_number} from local cache")
    else:
        # --- Shipday API fallback ---
        try:
            order_data = integration.get_shipday_order(order_id)
            if order_data:
                order_number = order_data.get("orderNumber")
                delivery = order_data.get("deliveryDetails", {})
                tracking_url = order_data.get("trackingUrl", "")
                logger.info(f"Order {order_id}: resolved activityId={order_number} from Shipday API")
            else:
                logger.warning(f"Order {order_id} not found in Shipday API (already purged) and not in cache")
        except Exception as e:
            logger.error(f"Error fetching order {order_id} from Shipday API: {e}")

    # order_number IS the activityId
    activity_id = order_number

    if not activity_id:
        logger.error(f"Order {order_id} has no order_number (not in payload or API), cannot submit to AVS")
        return {"order_id": order_id, "error": "No order_number/activityId - order may be purged before webhook processed"}

    # Convert timestamp (epoch ms) to ISO format
    visit_date = datetime.utcnow().isoformat() + 'Z'
    if timestamp:
        try:
            visit_date = datetime.utcfromtimestamp(int(timestamp) / 1000).isoformat() + 'Z'
        except (ValueError, TypeError, OSError):
            pass

    # Process any proof-of-delivery photos (unlikely for deleted orders)
    photos = []
    try:
        for idx, pod_url in enumerate(pods):
            if isinstance(pod_url, str) and pod_url.startswith("http"):
                base64_content, content_type = AVSShipdayIntegration.download_image_to_base64(pod_url)
                if base64_content:
                    photos.append({
                        "fileName": f"pod_{idx + 1}.jpg",
                        "base64Content": base64_content,
                        "contentType": content_type,
                        "latitude": 0.0,
                        "longitude": 0.0
                    })
    except Exception as e:
        logger.error(f"Error processing POD photos: {e}")

    # Build comments
    delete_reason = delivery_note or order_status or "Order deleted"
    comments = f"Verification returned - order deleted: {delete_reason}"

    # Build verification details - deleted = returned verification
    verification_details = {
        "customerName": delivery.get("name", ""),
        "address": delivery.get("address", ""),
        "visitDate": visit_date,
        "verificationStatus": VerificationStatus.RETURNED.value,
        "comments": comments,
        "reportUrl": tracking_url or "",
        "addressExists": False,
        "isResidentialAddress": False,
        "isCustomerResidence": False,
        "isCustomerKnown": False,
        "relationshipWithPersonMet": "Not applicable",
        "nameOfPersonMet": "Not applicable",
        "easeOfLocation": "Hard",
        "photos": photos
    }

    # Submit to AVS
    logger.info(f"[Phase 2] Submitting deleted/returned result to AVS for activityId: {activity_id}")
    avs_result = integration.submit_verification_result(
        activity_id=activity_id,
        shipday_order_data={},
        verification_details=verification_details
    )
    logger.info(f"[Phase 2] AVS submission result: success={avs_result.get('success')}, status={avs_result.get('status_code')}")

    return {
        "order_id": order_id,
        "order_number": order_number,
        "status": order_status,
        "delivery_name": delivery.get("name"),
        "delivery_note": delivery_note,
        "avs_submitted": avs_result.get("success", False),
        "avs_status_code": avs_result.get("status_code")
    }


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


@app.route("/api/v1/admin/refire-orders", methods=["POST"])
@require_api_key
def refire_orders():
    """Refire AVS submissions for a list of orders by fetching fresh POD photos from Shipday.
    ---
    tags:
      - Admin
    security:
      - ApiKeyAuth: []
      - BearerAuth: []
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required:
            - orders
          properties:
            orders:
              type: array
              items:
                type: object
                properties:
                  order_id:
                    type: integer
                  activity_id:
                    type: string
    responses:
      200:
        description: Refire results per order
    """
    data = request.get_json() or {}
    orders = data.get("orders", [])
    dry_run = data.get("dry_run", False)

    if not orders:
        return jsonify({"error": "No orders provided"}), 400

    try:
        integration = get_integration()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    results = []
    for item in orders:
        order_id = item.get("order_id")
        activity_id = item.get("activity_id")

        if not order_id or not activity_id:
            results.append({"order_id": order_id, "activity_id": activity_id, "error": "Missing order_id or activity_id"})
            continue

        try:
            # Fetch using order_number UUID (= activity_id) — numeric IDs return 404 for completed orders
            raw = integration.get_shipday_order(str(activity_id))
            if not raw:
                results.append({"order_id": order_id, "activity_id": activity_id, "error": "Could not fetch order from Shipday"})
                continue

            # Shipday returns a list when queried by order number
            fresh_order = raw[0] if isinstance(raw, list) and raw else raw

            photos = _extract_pod_photos_from_shipday_order(fresh_order, label="Refire")
            logger.info(f"[Refire] Order {order_id} — {len(photos)} photo(s) downloaded")

            # Extract customer/delivery details from Shipday API response structure
            customer = fresh_order.get("customer") or {}
            customer_name = customer.get("name") or ""
            address = customer.get("address") or ""
            tracking_url = fresh_order.get("trackingLink") or fresh_order.get("trackingUrl") or ""

            verification_details = {
                "customerName": customer_name,
                "address": address,
                "visitDate": datetime.utcnow().isoformat() + 'Z',
                "verificationStatus": VerificationStatus.SUCCESS.value,
                "comments": "Verification completed - refire with POD photos",
                "reportUrl": tracking_url or "https://avs-shipday-production.onrender.com",
                "addressExists": True,
                "isResidentialAddress": True,
                "isCustomerResidence": True,
                "isCustomerKnown": True,
                "relationshipWithPersonMet": "Self",
                "nameOfPersonMet": customer_name or "Not specified",
                "easeOfLocation": "Medium",
                "photos": photos
            }

            if dry_run:
                # Build the payload but do not submit — return it in full
                avs_response = integration._build_avs_response(activity_id, {}, verification_details)
                avs_payload = {
                    "vendorId": integration.avs_vendor_id,
                    "addressVerificationResponses": [avs_response]
                }
                results.append({
                    "order_id": order_id,
                    "activity_id": activity_id,
                    "pod_count": len(photos),
                    "dry_run": True,
                    "avs_payload": avs_payload
                })
                continue

            logger.info(f"[Refire] Submitting to AVS for activityId: {activity_id} with {len(photos)} photo(s)")
            avs_result = integration.submit_verification_result(
                activity_id=activity_id,
                shipday_order_data={},
                verification_details=verification_details
            )
            logger.info(f"[Refire] AVS result: success={avs_result.get('success')}, status={avs_result.get('status_code')}")

            results.append({
                "order_id": order_id,
                "activity_id": activity_id,
                "pod_count": len(photos),
                "avs_success": avs_result.get("success"),
                "avs_status_code": avs_result.get("status_code"),
                "avs_response": avs_result.get("response")
            })

        except Exception as e:
            logger.error(f"[Refire] Error for order {order_id}: {e}")
            results.append({"order_id": order_id, "activity_id": activity_id, "error": str(e)})

    return jsonify({"results": results, "total": len(results)})


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
