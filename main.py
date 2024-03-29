

import logging
import os
import re
import random
import hashlib
import hmac
import string
from string import letters
from datetime import datetime, timedelta

import jinja2
import webapp2

import urllib2
from xml.dom import minidom 
import json

from google.appengine.ext import db
from google.appengine.api import memcache

DEBUG = os.environ['SERVER_SOFTWARE'].startswith('Development')

SECRET = ''	
API_KEY = ''	

template_dir = os.path.join(os.path.dirname(__file__), 'templates')
jinja_env = jinja2.Environment(loader=jinja2.FileSystemLoader(template_dir),
										autoescape = True)
										

USER_AGENT = 'Mozilla/5.0 (Windows NT 6.1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2228.0 Safari/537.3'

HOSTIP_URL = "http://api.hostip.info/?ip="
def get_coords(ip):
	ip = "4.2.2.2"
	url = HOSTIP_URL + ip
	# must set user agent in request header. Some websiste forbid api call with no user-agent
	headers = {'User-Agent': USER_AGENT}
	req = urllib2.Request(url = url, headers = headers)
	content = None
	try:
		content = urllib2.urlopen(req).read()
	except:
		return
	
	if content:	
		d = minidom.parseString(content)
		coords = d.getElementsByTagName("gml:coordinates")
		if coords and coords[0].childNodes[0].nodeValue:
			lon, lat = coords[0].childNodes[0].nodeValue.split(',')
			return db.GeoPt(lat, lon)


GOOGLEMAPS_URL = ''									
def gmap_img(points):
	markers = '&'.join('markers=%s,%s' % (p.lat, p.lon) for p in points)
	return GOOGLEMAPS_URL + markers + '&key=' + API_KEY
										
def render_str(template, **params):
	t = jinja_env.get_template(template)
	return t.render(params)

def hash_str(s):
	return hmac.new(SECRET, s).hexdigest()

def make_secure_val(s):
	return "%s|%s" % (s, hash_str(s))

def check_secure_val(h):
	s = h.split('|')[0]
	if h == make_secure_val(s):
		return s
		
class BlogHandler(webapp2.RequestHandler):
	def write(self, *a, **kw):
		self.response.write(*a, **kw)
	
	def render_str(self, template, **params):
		params['user'] = self.user
		return render_str(template, **params)
	
	def render(self, template, **kw):
		self.write(self.render_str(template, **kw))
		
	def render_json(self, d):
		json_text = json.dumps(d)
		self.response.headers['Content-Type'] = 'application/json; charset=UTF-8'
		self.write(json_text)
		
	def set_secure_cookie(self, name, val):
		cookie_val = make_secure_val(val)
		self.response.headers.add_header(
			'Set-Cookie', 
			'%s=%s; Path=/' % (name, cookie_val))
			
	def read_secure_cookie(self, name):
		cookie_val = self.request.cookies.get(name)
		return cookie_val and check_secure_val(cookie_val)
		
	def login(self, user):
		self.set_secure_cookie('user_id', str(user.key().id()))
	
	def logout(self):
		self.response.headers.add_header('Set-Cookie', 'user_id=; Path=/')
	
	def initialize(self, *a, **kw):
		webapp2.RequestHandler.initialize(self, *a, **kw)
		uid = self.read_secure_cookie('user_id')
		self.user = uid and User.by_id(int(uid))
		
		if self.request.url.endswith('.json'):
			self.format = 'json'
		else:
			self.format = 'html'

##### user stuff
def make_salt(length = 5):
	return ''.join(random.choice(string.ascii_letters) for x in range(length))
	
def make_pw_hash(name, pw, salt = None):
	if not salt:
		salt = make_salt()
	h = hashlib.sha256(name + pw + salt).hexdigest()
	return '%s,%s' % (salt, h)

def valid_pw(name, password, h):
	salt = h.split(',')[0]
	return h == make_pw_hash(name, password, salt)
	
def users_key(group = 'default'):
	return db.Key.from_path('users', group)

class User(db.Model):
	name = db.StringProperty(required = True)
	pw_hash = db.StringProperty(required = True)
	email = db.StringProperty()

	@classmethod
	def by_id(cls, uid):
		return User.get_by_id(uid, parent = users_key())
	
	@classmethod
	def by_name(cls, name):
		u = User.all().filter('name =', name).get()
		return u
	
	@classmethod
	def register(cls, name, pw, email = None):
		pw_hash = make_pw_hash(name, pw)
		return User(parent = users_key(),
					name = name,
					pw_hash = pw_hash,
					email = email)
					
	@classmethod
	def login(cls, name, pw):
		u = cls.by_name(name)
		if u and valid_pw(name, pw, u.pw_hash):
			return u

##### blog stuff

def blog_key(name = 'default'):
    return db.Key.from_path('blogs', name)
		
class Post(db.Model):
	subject = db.StringProperty(required = True)
	content = db.TextProperty(required = True)
	created = db.DateTimeProperty(auto_now_add = True)
	last_modified = db.DateTimeProperty(auto_now = True)	
	coords = db.GeoPtProperty()
	
	def render(self):
		self._render_text = self.content.replace('\n', '<br>')
		return render_str("post.html", p = self)

	def as_dict(self):
		time_fmt = '%c'
		d = {'subject': self.subject,
			'content': self.content,
			'created' :self.created.strftime(time_fmt),
			'last_modified': self.last_modified.strftime(time_fmt)}
		return d


def age_set(key, val):
	save_time = datetime.utcnow()
	memcache.set(key, (val, save_time))
	
def age_get(key):
	r = memcache.get(key)
	if r:
		val, save_time = r
		age = (datetime.utcnow() - save_time).total_seconds()
	else:
		val, age = None, 0
	return val, age

def add_post(ip, post):
	post.put()
	get_posts(update = True)
	return str(post.key().id())
	
	
def get_posts(update = False):
	q = db.GqlQuery("select * from Post where ancestor is :1 order by created desc limit 10", blog_key())
	mc_key = 'BLOGS'
	posts, age = age_get(mc_key)
	if update or posts is None:
		logging.info("DB QUERY")
		posts = list(q)
		age_set(mc_key, posts)
	return posts, age

	# key = 'top'
	# posts = memcache.get(key)
	# if posts is None or update:
		# logging.error("DB QUERY")
		# posts = db.GqlQuery("select * from Post where ancestor is :1 order by created desc limit 10", blog_key())		
		# posts = list(posts)
		# memcache.set(key, posts)
	# return posts	

def age_str(age):
	s = 'queried %s seconds ago'
	age = int(age)
	if age == 1:
		s = s.replace('seconds', 'second')
	return s % age
	

### Blog Handlers		
class BlogFront(BlogHandler):		
	def get(self):		
		posts, age = get_posts()			
		points = filter(None, (p.coords for p in posts))
		
		if points:
			img_url = gmap_img(points)
		#self.write(repr(img_url))
		
		if self.format == 'html':
			self.render("front.html", posts = posts, age = age_str(age), img_url = img_url)
		else:
			self.render_json([p.as_dict() for p in posts])
		

class PostPage(BlogHandler):
	def get(self, post_id):
	
		post_key = 'POST_' + post_id		
		post, age = age_get(post_key)
		if not post:
			key = db.Key.from_path('Post', int(post_id), parent = blog_key())
			post = db.get(key)
			age_set(post_key, post)
			age = 0
		
		# id = int(post_id)
		# post = Post.get_by_id(id, parent=blog_key())
		
		if not post:
			self.error(404)
			return
		
		if self.format == 'html':		
			self.render("permalink.html", post=post, age = age_str(age))
		else:
			self.render_json(post.as_dict())
		
class NewPost(BlogHandler):
	def get(self):
		if self.user:
			self.render("newpost.html")
		else:
			self.redirect("/login")
		
	def post(self):
		if not self.user:
			self.redirect('/blog')
			
		subject = self.request.get("subject")
		content = self.request.get("content")
		ip = self.request.remote_addr
		
		if subject and content:
			p = Post(parent = blog_key(), subject = subject, content = content)
			coords = get_coords(ip)
			if coords:
				p.coords = coords
			
			p_id_str = add_post(ip, p)
			# p.put()	
			# get_posts(True)
			self.redirect('/blog/%s' % p_id_str)
		else:
			error = "subject and content, please!"
			self.render("newpost.html", subject=subject, content=content, error=error)

USER_RE = re.compile(r"^[a-zA-Z0-9_-]{3,20}$")
def valid_username(username):
    return username and USER_RE.match(username)

PASS_RE = re.compile(r"^.{3,20}$")
def valid_password(password):
    return password and PASS_RE.match(password)

EMAIL_RE  = re.compile(r'^[\S]+@[\S]+\.[\S]+$')
def valid_email(email):
    return not email or EMAIL_RE.match(email)
	
class SignUp(BlogHandler):
	def get(self):
		self.render('signup-form.html')
		
	def post(self):
		have_error = False
		self.username = self.request.get('username')
		self.password = self.request.get('password')
		self.verify = self.request.get('verify')
		self.email = self.request.get('email')
		
		params = dict(username = self.username, 
					email = self.email)
		
		if not valid_username(self.username):
			params['error_username'] = "That's not a valid username."
			have_error = True
			
		if not valid_password(self.password):
			params['error_password'] = "That's not a valid password."
			have_error = True
		elif self.password != self.verify:
			params['error_verify'] = "Your passwords do not match."
			have_error = True
			
		if not valid_email(self.email):
			params['error_email'] = "That's not a valid email."
			have_error = True
		
		if have_error:
			self.render('signup-form.html', **params)
		else:
			self.done()
	
	def done(self, *a, **kw):
		raise NotImplementedError
		
	
class Register(SignUp):
	def done(self):
		# make sure the user does not already exist
		u = User.by_name(self.username)
		if u:
			msg = "That user already exists."
			self.render('signup-form.html', error_username = msg)
		else:
			u = User.register(self.username, self.password, self.email)
			u.put()
			
			self.login(u)
			self.redirect('/blog')
			
class Login(BlogHandler):
	def get(self):
		self.render('login-form.html')
		
	def post(self):
		username = self.request.get('username')
		password = self.request.get('password')
		
		u = User.login(username, password)
		if u:
			self.login(u)
			self.redirect('/blog')
		else:
			msg = 'Invalid login.'
			self.render('login-form.html', error = msg)

class Logout(BlogHandler):
	def get(self):
		self.logout()
		self.redirect('/blog')
		
		
class Welcome(BlogHandler):
    def get(self):
        username = self.request.get('username')
        if valid_username(username):
            self.render('welcome.html', username = username)
        else:
            self.redirect('/signup')

app = webapp2.WSGIApplication([
    ('/', BlogFront),
	('/blog/?(?:\.json)?', BlogFront),
	('/blog/([0-9]+)(?:\.json)?', PostPage),
	('/blog/newpost', NewPost),
	('/signup', Register),
	('/login', Login),
	('/logout', Logout),
	('/welcome', Welcome)
], debug=True)


		  
