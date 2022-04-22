# -*- coding; utf-8 -*-
"""
Flask Blueprint that contains the custom error handlers for SCS, making sure
that machine interpretable JSON responses are returned in case of errors.

Use the register() and register_exception() functions to configure the errors
module to respond with custom errors for your blueprints.
"""
import re

from flask import Blueprint, jsonify, Response
from werkzeug.exceptions import HTTPException

bp = Blueprint('errors', __name__)

_non_word_chars_regex = re.compile(r'\W+')

# Error definitions are stored in the below variable. More can be added using
# the .register function. Note that, if an 'id' is not supplied to the abort
# function, the first key is used by default. If the code is not in this
# variable, the json_error_response function will generate a JSON response from
# the default exception name and description of flask
_definitions = {
    400: {
        'bad-request': (
            'Your request is invalid. For POST requests, check the JSON'
            ' payload format'
        )
    },
    401: {
        'unauthenticated': 'Invalid authentication credentials provided',
    },
    403: {
        'unauthorized': (
            'You are not authorized to access this resource'
        ),
    },
    405: {
        'method-not-allowed': (
            'This HTTP request method cannot be used on the endpoint'
        ),
    },
    500: {
        'internal-server-error': (
            'An internal server error occured'
        )
    }
}

# To return custom 500 error id's for specific exceptions, these can be added
# using the 'register_exception' function.
_exception_ids = []


def register(code: int, id_: str, message: str):
    """
    Registers an error id for a specific HTTP response code. To trigger the
    error, pass the id as a description to the flask abort command, e.g.:
    `abort(429, description={'id': 'rate-limited'})`

    Args:
        code: The HTTP response code to register the error for
        id_: A unique identifier, returned in the JSON response
        message: The message that should be returned in the JSON response

    Raises:
        ValueError: In case the given id_ is already registered to the code
    """
    code_errors = _definitions.setdefault(code, {})
    if id_ in code_errors:
        raise ValueError(
            f'CONFLICT: id {id_} is already registered for code {code}',
        )

    code_errors[id_] = message


def register_exception(
        exception_class: type, id_: str, *, message: str = None,
        ):
    """
    Registers a specific error id to be returned, if the given exception is
    raised somewhere in the code. If the given id is not yet registered for the
    500 status code, also a message should be provided.

    Note that if you don't want the server to return a 500 status code when
    the exception occurs, you should handle it in your own code, and call the
    'abort()' function, as described in the 'register_error' docstring.

    Args:
        exception_class:
            The class of the exception for which, if it occurs, the error with
            the given id should be returned
        id_:
            An error id, already registered for code 500 using the
            register_error function. Alternatively, if the 'message' parameter
            is provided, it will be registered as an error for the 500 status
            code
        message:
            When provided, the combination of the given id is also registered
            as an error for the 500 status code ()

    Raises:
        ValueError:
            In case the given Exception or id/message combination is already
            registered
    """
    for existing_class, _ in _exception_ids:
        if existing_class == exception_class:
            raise ValueError(
                f'CONFLICT: Exception {exception_class.__name__} is already '
                f'linked to id {id_}',
            )

    if message is not None:
        register(500, id_, message)

    _exception_ids.append((exception_class, id_))


def _error_response(code: int, id_: str, message: str) -> tuple[Response, int]:
    """
    Returns an SCS error response

    Args:
        code: Status code to return
        id_: Error id to return
        message: Error message to return

    Returns:
        The Flask response from the jsonify function and the response code
    """
    return jsonify({
        'error': {
            'id': id_,
            'message': message,
        }
    }), code


def _get_500_error_id(error: Exception) -> str:
    """
    Returns the error id to use for the given exception
    """
    id_ = next(iter(_definitions[500].keys()))
    for exception_cls, error_id in _exception_ids:
        if isinstance(error.original_exception, exception_cls):
            id_ = error_id
            break

    return id_


@bp.app_errorhandler(HTTPException)
def json_error_response(e) -> tuple[Response, int]:
    """
    Application-wide error handler to generate a JSON response in case of
    errors

    Args:
        e: The exception that triggered the handler

    Returns:
        The JSON response and status code to respond with to the user
    """
    if e.code == 500:
        id_ = _get_500_error_id(e)
        message = _definitions[e.code][id_]
    elif e.code in _definitions:
        # By default, use the first error id that is configured. If the
        # description object contains an 'id' key, that id will be used if
        # available
        id_ = next(iter(_definitions[e.code].keys()))
        if isinstance(e.description, dict):
            id_ = e.description.get('id', id_)
        message = _definitions[e.code][id_]
    else:
        # In case no definition is given, the original error name is converted
        # to an id, and the description is used as the message
        id_ = _non_word_chars_regex.sub('-', e.name.lower())
        message = e.description

    return _error_response(
        e.code,
        id_,
        message,
    )
