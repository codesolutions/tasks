import logging
import os
import inc.config_manager

LOG_FILE = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)),
    "debug.log"
)

logging.basicConfig(filename=LOG_FILE,
                    filemode='a',
                    format='%(asctime)s,%(msecs)03d %(name)s %(levelname)s %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    level=logging.DEBUG)

logging.info("Logging starts")


def get_jira_ticket_from_url(url):
    return url.replace(f"{inc.config_manager.config.get('JIRA_URL')}/browse/", "")

def t(key, **kwargs):
    """Gets a translated string by its key."""
    template = inc.config_manager.STRINGS.get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, TypeError):
            return f"FORMAT_ERROR_FOR_KEY: {key}"
    return template