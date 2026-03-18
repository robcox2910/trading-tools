"""Shared HTTP status code constants for all API client modules.

Centralise status codes in one place to avoid redefinition across clients.
"""

HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401
HTTP_NOT_FOUND = 404
HTTP_TOO_MANY_REQUESTS = 429
HTTP_INTERNAL_ERROR = 500
