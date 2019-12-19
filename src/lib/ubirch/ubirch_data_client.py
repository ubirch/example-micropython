import binascii
import json
import time

import logging
import umsgpack as msgpack
import urequests as requests
from uuid import UUID
from .ubirch_client import UbirchClient

logger = logging.getLogger(__name__)


class UbirchDataClient:

    def __init__(self, uuid: UUID, cfg: dict):
        """
        Initialize the ubirch client with the service URLs and header with device UUID and password for authentication.
        """
        self._uuid = uuid
        self._auth = cfg['password']
        self._headers = {
            'X-Ubirch-Hardware-Id': str(uuid),
            'X-Ubirch-Credential': binascii.b2a_base64(self._auth).decode('utf-8').rstrip('\n'),
            'X-Ubirch-Auth-Type': 'ubirch'
        }
        if 'data' in cfg:
            self._data_service_url = cfg['data']
        else:
            self._data_service_url = "https://data.{}.ubirch.com/v1/msgPack".format(cfg['env'])

        if 'keyService' in cfg:
            key_service_url = cfg['keyService']
        else:
            key_service_url = "https://key.{}.ubirch.com/api/keyService/v1/pubkey/mpack".format(cfg['env'])

        if 'niomon' in cfg:
            auth_service_url = cfg['niomon']
        else:
            auth_service_url = "https://niomon.{}.ubirch.com".format(cfg['env'])

        # this client generates a new key pair and registers the public key at the key service
        self._ubirch = UbirchClient(uuid, self._headers, key_service_url, auth_service_url)

        self._msg_type = 1

    def pack_message_msgpack(self, data: dict) -> (bytes, bytes):
        """
        Generate a message for sending to the ubirch data service.
        :param data: a map containing the data to be sent
        :return: a msgpack formatted array with the device UUID, message type, timestamp and data
        :return: the hash over the data message
        """
        msg = [
            self._uuid.bytes,
            self._msg_type,
            int(time.time()),
            data,
            0
        ]

        # calculate hash of message (without last array element)
        serialized = msgpack.packb(msg)[0:-1]
        message_hash = self._ubirch.hash(serialized)

        # replace last element in array with the hash
        msg[-1] = message_hash
        serialized = msgpack.packb(msg)
        print(binascii.hexlify(serialized).decode())

        return serialized, message_hash

    def pack_message_json(self, data: dict) -> (dict, bytes):
        """
        Generate a message for sending to the ubirch data service.
        :param data:  a map containing the data to be sent
        :return: a map with the device UUID, message type, timestamp and data
        :return: the hash over the data message
        """
        msg_map = {
            'uuid': str(self._uuid),
            'msg_type': self._msg_type,
            'timestamp': int(time.time()),
            'data': data
        }

        # calculate hash of message
        message_hash = self._ubirch.hash(json.dumps(msg_map))

        # append hash to data map
        msg_map.update({
            'hash': binascii.b2a_base64(message_hash).decode('utf-8').rstrip('\n')
        })
        print(msg_map)

        return msg_map, message_hash

    def send_msgpack(self, data: dict):
        """
        Send data message to msgpack endpoint
        :param data: a map containing the data to be sent
        :return: the http response, the hash over the data message
        """
        # pack data in a msgpack formatted message with device UUID, message type and timestamp
        message, message_hash = self.pack_message_msgpack(data)

        # send message to ubirch data service (only send UPP if successful)
        r = requests.post(self._data_service_url, headers=self._headers, data=binascii.hexlify(message))
        return r, message_hash

    def send_json(self, data: dict):
        """
        Send data message to json endpoint
        :param data: a map containing the data to be sent
        :return: the http response, the hash over the data message
        """
        msg_map, message_hash = self.pack_message_json(data)

        # request needs to be sent twice because of bug in backend
        r = requests.post(self._data_service_url, headers=self._headers, json=msg_map)
        r.close()
        r = requests.post(self._data_service_url, headers=self._headers, json=msg_map)
        return r, message_hash

    def send(self, data: dict):
        """
        Pack the data with UUID and timestamp and send to ubirch data service. On success, send certificate
        of the message to ubirch authentication service. Throws exception if message couldn't be sent or
        response couldn't be verified.
        :param data: a map containing the data to be sent
        """
        print("\n** sending measurements to {} ...".format(self._data_service_url))
        if self._data_service_url.endswith("msgPack"):
            r, message_hash = self.send_msgpack(data)
        else:
            r, message_hash = self.send_json(data)

        if r.status_code == 200:
            print("** data sent")
            r.close()
            # send UPP to niomon
            self._ubirch.send(message_hash)
        else:
            raise Exception(
                "!! request to {} failed with status code {}: {}".format(self._data_service_url, r.status_code,
                                                                         r.text))
