"""
Tests for the scs.auth module


Copyright 2022 Tom Brouwer

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
from pathlib import Path
import tools

file_dir = Path(__file__).parent.absolute()
config_dir = Path(file_dir, 'data/1')
client = tools.get_test_client(config_dir)
tokens = tools.safe_load_yaml_file(
    Path(file_dir, 'data/1/secrets/scs-tokens.yaml')
)


def test_if_user_authenticates():
    """
    Bearer authentication must be functional
    """
    response = client.get(
        '/configs/elasticsearch/elasticsearch.yml',
        headers={
            'Authorization': (
                f"Bearer {tokens['test-user']}"
            ),
        },
        environ_base={'REMOTE_ADDR': '127.0.0.1'},
    )
    assert response.status_code == 200


def test_wrong_credentials_and_limiting():
    """
    Test (1) if users with bad credentials are denied access, and (2) if the
    rate-limiter kicks in after too many faulty authentication requests

    Note: If this test is run exactly on the breakpoint of two consecutive 15
    minute windows, it may fail
    """
    for i in range(11):
        response = client.get(
            '/configs/elasticsearch/elasticsearch.yml',
            headers={
                'Authorization': (
                    'Bearer WrongBearerToken'
                ),
            },
            environ_base={'REMOTE_ADDR': '127.0.0.1'},
        )
        if i < 10:
            assert response.status_code == 401
        else:
            assert response.status_code == 429


def test_path_access_denied():
    """
    Test is access is granted or denied as expected based on the allowed paths
    for each user
    """
    response = client.get(
        '/configs/host-name',
        headers={
            'Authorization': (
                f"Bearer {tokens['test-user']}"
            ),
        },
        environ_base={'REMOTE_ADDR': '192.168.1.34'}
    )
    assert response.status_code == 403
    assert response.get_json()['error']['id'] == 'unauthorized-path'

    response = client.get(
        '/configs/tags_*.json',
        headers={
            'Authorization': (
                f"Bearer {tokens['test-user']}"
            ),
        },
        environ_base={'REMOTE_ADDR': '192.168.1.34'}
    )
    rdata = response.get_json(force=True)
    assert rdata['Just an example for'] == 'Testing escaped wildcards'
    assert response.status_code == 200

    response = client.get(
        '/configs/tags_x.json',
        headers={
            'Authorization': (
                f"Bearer {tokens['test-user']}"
            ),
        },
        environ_base={'REMOTE_ADDR': '192.168.1.34'}
    )
    assert response.status_code == 403
    assert response.get_json()['error']['id'] == 'unauthorized-path'

    response = client.get(
        '/configs/host-name',
        headers={
            'Authorization': (
                f"Bearer {tokens['test-user-2']}"
            ),
        },
        environ_base={'REMOTE_ADDR': '192.168.1.34'}
    )
    assert response.status_code == 200


def test_global_whitelisted_but_not_user_whitelisted():
    """
    An IP address that is globally whitelisted, but not whitelisted for the
    specific user must trigger a 403 error
    """
    response = client.get(
        '/configs/elasticsearch/elasticsearch.yml',
        headers={
            'Authorization': (
                f"Bearer {tokens['test-user']}"
            ),
        },
        environ_base={'REMOTE_ADDR': '192.168.1.2'}
    )
    assert response.status_code == 403
    assert response.get_json()['error']['id'] == 'unauthorized-ip'


def test_not_globally_whitelisted():
    """
    An IP address that is not globally whitelisted must trigger a
    403 error
    """
    response = client.get(
        '/configs/elasticsearch/elasticsearch.yml',
        headers={
            'Authorization': (
                f"Bearer {tokens['test-user']}"
            ),
        },
        environ_base={'REMOTE_ADDR': '172.16.94.2'}
    )
    assert response.status_code == 403
    assert response.get_json()['error']['id'] == 'unauthorized-ip'
