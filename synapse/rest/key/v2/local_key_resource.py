# -*- coding: utf-8 -*-
# Copyright 2014, 2015 OpenMarket Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from twisted.web.resource import Resource
from synapse.http.server import respond_with_json_bytes
from syutil.crypto.jsonsign import sign_json
from syutil.base64util import encode_base64
from syutil.jsonutil import encode_canonical_json
from hashlib import sha256
from OpenSSL import crypto
import logging


logger = logging.getLogger(__name__)


class LocalKey(Resource):
    """HTTP resource containing encoding the TLS X.509 certificate and NACL
    signature verification keys for this server::

        GET /_matrix/key/v2/server/a.key.id HTTP/1.1

        HTTP/1.1 200 OK
        Content-Type: application/json
        {
            "valid_until_ts": # integer posix timestamp when this result expires.
            "server_name": "this.server.example.com"
            "verify_keys": {
                "algorithm:version": {
                    "key": # base64 encoded NACL verification key.
                }
            },
            "old_verify_keys": {
                "algorithm:version": {
                    "expired_ts": # integer posix timestamp when the key expired.
                    "key": # base64 encoded NACL verification key.
                }
            }
            "tls_certificate": # base64 ASN.1 DER encoded X.509 tls cert.
            "signatures": {
                "this.server.example.com": {
                   "algorithm:version": # NACL signature for this server
                }
            }
        }
    """

    isLeaf = True

    def __init__(self, hs):
        self.version_string = hs.version_string
        self.config = hs.config
        self.clock = hs.clock
        self.update_response_body(self.clock.time_msec())
        Resource.__init__(self)

    def update_response_body(self, time_now_msec):
        refresh_interval = self.config.key_refresh_interval
        self.valid_until_ts = int(time_now_msec + refresh_interval)
        self.response_body = encode_canonical_json(self.response_json_object())

    def response_json_object(self):
        verify_keys = {}
        for key in self.config.signing_key:
            verify_key_bytes = key.verify_key.encode()
            key_id = "%s:%s" % (key.alg, key.version)
            verify_keys[key_id] = {
                u"key": encode_base64(verify_key_bytes)
            }

        old_verify_keys = {}
        for key in self.config.old_signing_keys:
            key_id = "%s:%s" % (key.alg, key.version)
            verify_key_bytes = key.encode()
            old_verify_keys[key_id] = {
                u"key": encode_base64(verify_key_bytes),
                u"expired_ts": key.expired,
            }

        x509_certificate_bytes = crypto.dump_certificate(
            crypto.FILETYPE_ASN1,
            self.config.tls_certificate
        )

        sha256_fingerprint = sha256(x509_certificate_bytes).digest()

        json_object = {
            u"valid_until_ts": self.valid_until_ts,
            u"server_name": self.config.server_name,
            u"verify_keys": verify_keys,
            u"old_verify_keys": old_verify_keys,
            u"tls_fingerprints": [{
                u"sha256": encode_base64(sha256_fingerprint),
            }]
        }
        for key in self.config.signing_key:
            json_object = sign_json(
                json_object,
                self.config.server_name,
                key,
            )
        return json_object

    def render_GET(self, request):
        time_now = self.clock.time_msec()
        # Update the expiry time if less than half the interval remains.
        if time_now + self.config.key_refresh_interval / 2 > self.valid_until_ts:
            self.update_response_body(time_now)
        return respond_with_json_bytes(
            request, 200, self.response_body,
            version_string=self.version_string
        )
