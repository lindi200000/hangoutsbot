import asyncio, base64, io, imghdr, json, logging, time

from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from utils import simple_parse_to_segments

logger = logging.getLogger(__name__)


class BaseBotRequestHandler(BaseHTTPRequestHandler):
    _bot = None # set externally by the hangupsbot sink loader
    sinkname = "UNKNOWN"

    def __init__(self, *args):
        self.sinkname = self.__class__.__name__
        BaseHTTPRequestHandler.__init__(self, *args)


    def do_POST(self):
        """handle incoming POST request
        acquire the path, any query string (?abc=xyz), sent content
        """
        logger.debug('{}: receiving POST...'.format(self.sinkname))

        content = self.rfile.read(int(self.headers['Content-Length'])).decode('UTF-8')
        self.send_response(200)
        message = bytes('OK', 'UTF-8')
        self.send_header("Content-type", "text")
        self.send_header("Content-length", str(len(message)))
        self.end_headers()
        self.wfile.write(message)
        logger.debug('{}: connection closed'.format(self.sinkname))

        # parse requested path + query string
        _parsed = urlparse(self.path)
        path = _parsed.path
        query_string = parse_qs(_parsed.query)

        logger.debug("{}: incoming: {} {} {} bytes".format(self.sinkname, path, query_string, len(content)))

        # process the payload
        try:
            asyncio.async(
                self.process_request(path, query_string, content)
            ).add_done_callback(lambda future: future.result())

        except Exception as e:
            logging.exception(e)


    @asyncio.coroutine
    def process_request(self, path, query_string, content):
        """default handler for incoming request
        path should contain a conversation id e.g. http://localhost/XXXXXXXXXXX/
        content is a valid json string with keys:
            echo                html string
            image 
                base64encoded   base64-encoded image data
                filename        optional filename (else determined automatically via imghdr)
        """
        # parse incoming data
        payload = json.loads(content)

        path = path.split("/")
        conversation_id = path[1]
        if not conversation_id:
            logger.error("{}: conversation id must be provided as part of path".format(self.sinkname))
            return

        html = None
        if "echo" in payload:
            html = payload["echo"]

        image_data = None
        image_filename = None
        if "image" in payload:
            if "base64encoded" in payload["image"]:
                image_raw = base64.b64decode(payload["image"]["base64encoded"])
                image_data = io.BytesIO(image_raw)

            if "filename" in payload["image"]:
                image_filename = payload["image"]["filename"]
            else:
                image_type = imghdr.what('ignore', image_raw)
                image_filename = str(int(time.time())) + "." + image_type
                logging.info("automatic image filename: {}".format(image_filename))

        if not html and not image_data:
            logger.debug("{}: nothing to send".format(self.sinkname))
            return

        yield from self.send_data(conversation_id, html, image_data=image_data, image_filename=image_filename)


    @asyncio.coroutine
    def send_data(self, conversation_id, html, image_data=None, image_filename=None):
        """sends html and/or image to a conversation
        image_filename is recommended but optional, fallbacks to <timestamp>.jpg if undefined
        process_request() should determine the image extension prior to this
        """
        image_id = None
        if image_data:
            if not image_filename:
                image_filename = str(int(time.time())) + ".jpg"
                logging.warning("fallback image filename: {}".format(image_filename))

            image_id = yield from self._bot._client.upload_image(image_data, filename=image_filename)

        if not html and not image_id:
            logger.debug("{}: nothing to send".format(self.sinkname))
            return

        segments = simple_parse_to_segments(html)
        logger.debug("{}: sending segments: {}".format(self.sinkname, len(segments)))

        yield from self._bot.coro_send_message(conversation_id, segments, context=None, image_id=image_id)


    def log_error(self, format_string, *args):
        logging.error("{} - {} {}".format(self.sinkname, self.address_string(), format_string%args))

    def log_message(self, format_string, *args):
        logging.info("{} - {} {}".format(self.sinkname, self.address_string(), format_string%args))
