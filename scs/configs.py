# -*- coding; utf-8 -*-
"""
Flask Blueprint containing the code for the configs/ endpoints, that loads the
configuration data and returns the formatted data
"""
from pathlib import Path
from functools import partial
import copy

from flask import (
    Blueprint, request, g, make_response, abort, current_app
)
from flask.blueprints import BlueprintSetupState
import fastjsonschema

from . import yaml
from .tools import get_object_from_name

bp = Blueprint('configs', __name__, url_prefix='/configs')

# Configure ninja2
file_folder = Path(__file__).absolute().parent
url_structure = {}

env_file_schema_path = Path(
    Path(__file__).absolute().parent,
    'schemas/scs-env.yaml'
)
env_file_schema = yaml.safe_load_file(env_file_schema_path)
validate_env_file = fastjsonschema.compile(env_file_schema)

# The default values are populsated from the JSON schema defaults
DEFAULT_ENV = validate_env_file({})


@bp.record
def init(setup_state: BlueprintSetupState):
    """
    Initializes the configs/* endpoints

    Args:
        setup_state:
            The .options attribute of this (options passed to
            register_blueprint function) should contain the following dict
            data:
                directories: dict; containing:
                    common, config, secrets: str


    """
    global config_basepath, common_basepath, secrets_basepath

    scs_config = setup_state.app.config['SCS']

    config_basepath = Path(scs_config['directories']['config']).absolute()
    common_basepath = Path(scs_config['directories']['common']).absolute()
    secrets_basepath = Path(scs_config['directories']['secrets']).absolute()
    add_constructors = scs_config['extensions']['constructors']
    check_templates = scs_config['templates']['validate_on_startup']
    default_rendering_options = scs_config['templates']['rendering_options']
    validate_dots = scs_config['environments']['reject_keys_containing_dots']
    load_on_demand = not scs_config['environments']['cache']

    _initialize_yaml_loaders(
        common_dir=common_basepath,
        secrets_dir=secrets_basepath,
        add_constructors=add_constructors,
        validate_dots=validate_dots
    )

    # Configure template rendering options
    setup_state.app.jinja_options.update(default_rendering_options)

    bp.template_folder = config_basepath

    relative_config_template_paths = get_relative_config_template_paths()
    for relative_url in relative_config_template_paths:
        if load_on_demand:
            envdata = None
        else:
            envdata = load_env(relative_url.lstrip('/'))

        bp.add_url_rule(
            relative_url,
            # Endpoint name must be unique, but  may not contain a dot
            endpoint=relative_url.replace('.', '_'),
            view_func=partial(
                view_config_file,
                path=relative_url,
                envdata=envdata
            ),
            methods=['GET', 'POST'],
        )

        if check_templates and not load_on_demand:
            testenv = copy.deepcopy(envdata)
            serialize_secrets(testenv)
            template = setup_state.app.jinja_env.get_template(
                relative_url.lstrip('/')
            )
            template.render(**testenv)


def _initialize_yaml_loaders(
        *, common_dir: Path, secrets_dir: Path, add_constructors: list[dict],
        validate_dots: bool,
        ):
    """
    Initialize the loader with the right constructors

    Args:
        common_dir: The base directory used to resolve !scs-common tags

        secrets_dir: The base directory used to resolve !scs-secret tags

        add_constructors: List of custom constructers to add

        validate_dots: Whether errors should be generated if dots are in keys
    """
    ENV_FILE_CONSTRUCTORS = [
        yaml.SCSRelativeConstructor(
            validate_dots=validate_dots,
        ),
        yaml.SCSSecretConstructor(
            secrets_dir=secrets_dir,
            validate_dots=validate_dots,
        ),
        yaml.SCSCommonConstructor(
            common_dir=common_dir,
            validate_dots=validate_dots,
        ),
        yaml.SCSExpandEnvConstructor(),
    ]

    if add_constructors:
        for constructor_config in add_constructors:
            constructor_name = constructor_config['name']
            constructor_class = get_object_from_name(constructor_name)
            if not isinstance(constructor_class, yaml.SCSYamlTagConstructor):
                raise ValueError(
                    f"The constructor '{constructor_name}' is not a "
                    "SCSYamlTagConstructor subclass"
                )
            options = constructor_config.get('options', {})
            ENV_FILE_CONSTRUCTORS.append(
                constructor_class(**options)
            )

    SECRET_FILE_CONSTRUCTORS = [
        yaml.SCSGenSecretConstructor(),
    ]

    for constructor in ENV_FILE_CONSTRUCTORS:
        yaml.SCSEnvFileLoader.add_constructor(
            constructor.tag, constructor.construct
        )

    for constructor in SECRET_FILE_CONSTRUCTORS:
        yaml.SCSSecretFileLoader.add_constructor(
            constructor.tag, constructor.construct
        )


class EnvFileFormatException(Exception):
    """Raised if JSON schema validation fails on an env-file"""


def load_env_file(relative_path: str) -> dict:
    """
    Load the data from the given env file, if it exists
    """
    path = Path(config_basepath, relative_path)
    if not path.is_file():
        return {}

    env_data = yaml.load_file(path, loader=yaml.SCSEnvFileLoader)

    try:
        # Ignore the return, since we don't want to fill defaults
        validate_env_file(env_data)
    except fastjsonschema.JsonSchemaValueException as e:
        raise EnvFileFormatException(
            f'The env file {path.as_posix()} failed validation: {e.message}'
        )

    return env_data


def get_env_file_hierarchy(relative_path: str) -> list[str]:
    """Gets all possible relative paths of env files"""
    path_parts = relative_path.split('/')

    ordered_envfiles = ['scs-env.yaml']
    envfile_basepath = ''
    for part in path_parts[:-1]:
        envfile_basepath += f'{part}/'
        ordered_envfiles.append(envfile_basepath + '/scs-env.yaml')
    ordered_envfiles.append(relative_path + '.scs-env.yaml')

    return ordered_envfiles


def serialize_secrets(data: dict | list) -> list[str]:
    """
    Serialize all secrets in the data

    Args:
        env_data:
            The environment data, possible containg SCSSecret objects. This
            will be serialized IN PLACE

    Returns:
        The id's of the secrets that were serialized
    """
    secret_ids = set()
    if isinstance(data, list):
        for i, item in enumerate(data):
            if isinstance(item, yaml.SCSSecret):
                secret_ids.add(item.id)
                data[i] = item.value
            else:
                serialize_secrets(item)
    elif isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                serialized_secrets = serialize_secrets(value)
                secret_ids.update(serialized_secrets)
            elif isinstance(value, yaml.SCSSecret):
                secret_ids.add(value.id)
                data[key] = value.value

    return secret_ids


def load_env(relative_path):
    """
    Load the full environment for the given relative path
    """
    combined_env = copy.deepcopy(DEFAULT_ENV)
    rel_env_file_paths = get_env_file_hierarchy(relative_path)

    for rel_path in rel_env_file_paths:
        data = load_env_file(rel_path)
        for key, value in data.items():
            if isinstance(value, dict):
                combined_env[key].update(value)
            else:
                combined_env[key] = value

        combined_env.update(data)

    return combined_env


def get_relative_config_template_paths() -> list[str]:
    """
    Get the relative paths of all config templates
    """
    config_template_paths = []
    for path in config_basepath.rglob('*'):
        if path.is_file() and not path.name.endswith('scs-env.yaml'):
            relative_template_path = \
                path.as_posix().removeprefix(config_basepath.as_posix())
            config_template_paths.append(relative_template_path)

    return config_template_paths


# These seem to erroneously not be supported on the overlay function
# https://github.com/pallets/jinja/issues/1645
MISSING_OVERLAY_OPTIONS = [
    'newline_sequence',
    'keep_trailing_newline'
]


def view_config_file(path: str, envdata: dict):
    """
    Flask view for the file at the given path, with the given envdata
    """
    if envdata is None:
        env = load_env(path.lstrip('/'))
    else:
        env = copy.deepcopy(envdata)

    if request.method not in envdata['methods']:
        abort(405)

    if request.method == 'POST':
        env['context'].update(request.get_json(force=True))

    secret_ids = serialize_secrets(env)

    if rendering_options := env['rendering_options']:
        # Since some options seem to erroneously not be supported, these are
        # applied later
        # https://github.com/pallets/jinja/issues/1645
        unsupported_options = {}
        for key in MISSING_OVERLAY_OPTIONS:
            if key in rendering_options:
                unsupported_options[key] = rendering_options.pop(key)
        jinja_env = current_app.jinja_env.overlay(**rendering_options)
        for key, value in unsupported_options.items():
            setattr(jinja_env, key, value)
    else:
        jinja_env = current_app.jinja_env

    template = jinja_env.get_template(path.lstrip('/'))
    rendered_template = template.render(env['context'])

    response = make_response(rendered_template)
    response.headers.clear()  # Remove the default 'html' content type
    response.headers.update(env['headers'])
    response.status = env['status']

    g.add_audit_event(event_type='config-loaded')
    if secret_ids:
        g.add_audit_event(
            event_type='secrets-loaded', secrets=list(secret_ids),
        )

    return response
