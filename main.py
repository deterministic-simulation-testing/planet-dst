import argparse
import dataclasses
import datetime
import email
import requests
import sys
import yaml
import jinja2
from lxml import etree

NAMESPACES = {
    'atom': 'http://www.w3.org/2005/Atom',
    'content': 'http://purl.org/rss/1.0/modules/content/'
}
DEBUG = False

@dataclasses.dataclass
class Author(object):
    name : str = None
    configname : str = None
    uri : str = None
    email : str = None

@dataclasses.dataclass
class FeedSource(object):
    id_ : str
    title : str
    link : str
    author : Author = None
    updated : str = None

@dataclasses.dataclass
class Post(object):
    title : str
    published : str
    id_ : str
    link : str
    source : FeedSource
    summary : str = None
    content : str = None

def isoformat_to_rfc3339(isofmt):
    if isofmt[-6] == '+' and isofmt[-3] == ':':
        isofmt = isofmt[0:-6]
    if not isofmt.endswith('Z'):
        isofmt = isofmt + 'Z'
    return isofmt

def extract_rss_feedsource(feed):
    feed = feed.find('channel')
    title = feed.find('title').text
    if feed.find('lastBuildDate') is not None:
        updated = feed.find('lastBuildDate').text
        updated = email.utils.parsedate_to_datetime(updated).isoformat()[0:-6] + 'Z'
    link = feed.find('link').text
    id_ = link
    args = locals()
    del args['feed']
    return FeedSource(**args)

def extract_rss_post(source, entry):
    title = entry.find('title').text
    published = entry.find('pubDate').text
    published = email.utils.parsedate_to_datetime(published).isoformat()[0:-6] + 'Z'
    if entry.find('content:encoded', namespaces=NAMESPACES) is not None:
        content = entry.find('content:encoded', namespaces=NAMESPACES).text
        if entry.find('description') is not None:
            summary = entry.find('description').text
    else:
        content = entry.find('description').text
    id_ = entry.find('guid').text
    link = entry.find('link').text
    args = locals()
    del args['entry']
    return Post(**args)

def extract_atom_feedsource(feed):
    title = feed.find('atom:title', namespaces=NAMESPACES).text
    updated = feed.find('atom:updated', namespaces=NAMESPACES).text
    link = feed.find('atom:link', namespaces=NAMESPACES).get('href')
    id_ = feed.find('atom:id', namespaces=NAMESPACES).text
    def author(feed):
        author = feed.find('atom:author', namespaces=NAMESPACES)
        if author is not None:
            name = author.find('atom:name', namespaces=NAMESPACES).text
            uri = author.find('atom:uri', namespaces=NAMESPACES)
            if uri is not None: uri = uri.text
            email = author.find('atom:email', namespaces=NAMESPACES)
            if email is not None: email = email.text
            return Author(name=name, uri=uri, email=email)
        return None
    author = author(feed)
    args = locals()
    del args['feed']
    return FeedSource(**args)

def extract_atom_post(source, entry):
    title = entry.find('atom:title', namespaces=NAMESPACES).text
    published = entry.find('atom:published', namespaces=NAMESPACES).text
    content = entry.find('atom:content', namespaces=NAMESPACES)
    content = content.text if content is not None else None
    summary = entry.find('atom:summary', namespaces=NAMESPACES)
    summary = summary.text if summary is not None else None
    id_ = entry.find('atom:id', namespaces=NAMESPACES).text
    link = entry.find('atom:link', namespaces=NAMESPACES).get('href')
    args = locals()
    del args['entry']
    return Post(**args)

def matches_category(spec, entry):
    if spec is None:
        return False

    post_categories = entry.xpath('.//category | .//atom:category', namespaces=NAMESPACES)
    for allowed in spec:
        for postcat in post_categories:
            for (key, value) in allowed.items():
                if key == 'text':
                    if postcat.text != value:
                        break
                elif postcat.get(key) != value:
                    break
            else:
                return True

    return False

def matches_posts(spec, entry):
    if spec is None:
        return False
    
    for allowed in spec:
        for (key, value) in allowed.items():
            element = entry.find(key, namespaces=NAMESPACES)
            if element is not None and element.text == value:
                return True

    return False

def posts_to_atom(site_config, posts, f):
    rfc3339now = isoformat_to_rfc3339(datetime.datetime.now().isoformat())
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader("."),
        autoescape=jinja2.select_autoescape(["html", "xml"])
    )
    atom_template = env.get_template("atom.jinja2")
    atom_xml = atom_template.render(site_config=site_config, entries=posts, rfc3339now=rfc3339now)
    f.write(atom_xml)

def posts_to_html(site_config, posts, f):
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader("."),
        autoescape=jinja2.select_autoescape(["html", "xml"])
    )
    html_template = env.get_template("html.jinja2")
    html_content = html_template.render(site_config=site_config, posts=posts)
    f.write(html_content)

def parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', help="Fetch only the feed of this name")
    parser.add_argument('--atom', help="Output filename for combined atom feed")
    parser.add_argument('--html', help="Output filename for html site")
    parser.add_argument('--debug', action='store_true')
    return parser.parse_args(argv)

def main(argv):
    args = parse_args(argv)
    if args.debug:
        global DEBUG
        DEBUG = True
    config = yaml.safe_load(open('feeds.yaml'))
    feeds = config['feeds']
    aggregated_posts = []
    for feed in feeds:
        if args.name is not None and feed['name'] != args.name:
            continue
        print(f"Fetching {feed['name']} from {feed['url']}")
        xmldata = requests.get(feed['url']).content
        xmlfeed = etree.fromstring(xmldata)

        feedtype = None
        if xmlfeed.xpath('/rss'):
            feedtype = 'rss'
            feedsource = extract_rss_feedsource(xmlfeed)
        if xmlfeed.xpath('/atom:feed', namespaces=NAMESPACES):
            feedtype = 'atom'
            feedsource = extract_atom_feedsource(xmlfeed)
        assert(feedtype is not None)
        if feedsource.author is not None:
            feedsource.author.configname = feed['name']
        else:
            feedsource.author = Author(configname=feed['name'])
        
        entries = xmlfeed.xpath('//atom:entry | //item', namespaces=NAMESPACES)
        for entry in entries:
            should_include = (feed.get('include_all') or
                              matches_category(feed.get('category'), entry) or
                              matches_posts(feed.get('posts'), entry))
            if should_include:
                if feedtype == 'rss': aggregated_posts.append(extract_rss_post(feedsource, entry))
                if feedtype == 'atom': aggregated_posts.append(extract_atom_post(feedsource, entry))

    aggregated_posts.sort(key=lambda x: datetime.datetime.fromisoformat(x.published))
    aggregated_posts.reverse()
    if args.atom is not None:
        posts_to_atom(config['site'], aggregated_posts, open(args.atom, 'w'))
    if args.html is not None:
        posts_to_html(config['site'], aggregated_posts, open(args.html, 'w'))


if __name__ == "__main__":
    main(sys.argv[1:])
