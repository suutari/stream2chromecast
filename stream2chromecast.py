#!/usr/bin/env python
"""
stream2chromecast.py: Chromecast media streamer for Linux

author: Pat Carter - https://github.com/Pat-Carter/stream2chromecast

version: 0.6.1

"""


# Copyright (C) 2014-2016 Pat Carter
#
# This file is part of Stream2chromecast.
#
# Stream2chromecast is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Stream2chromecast is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Stream2chromecast.  If not, see <http://www.gnu.org/licenses/>.


VERSION = "0.6.1"


import sys, os, errno
import signal
import argparse

from cc_media_controller import CCMediaController
import cc_device_finder
import time

import BaseHTTPServer
import urllib
import mimetypes
from threading import Thread

import subprocess

import httplib
import urlparse

import socket

import tempfile


PIDFILE = os.path.join(tempfile.gettempdir(), "stream2chromecast_%s.pid") 

FFMPEG = 'ffmpeg -i "%s" -preset ultrafast -f mp4 -frag_duration 3000 -b:v 2000k -loglevel error %s -'
AVCONV = 'avconv -i "%s" -preset ultrafast -f mp4 -frag_duration 3000 -b:v 2000k -loglevel error %s -'



class RequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    content_type = "video/mp4"
    
    """ Handle HTTP requests for files which do not need transcoding """
    
    def do_GET(self):
        filepath = urllib.unquote_plus(self.path)
        
        self.suppress_socket_error_report = None
        
        self.send_headers(filepath)       
        
        print "sending file"       
        try: 
            self.write_response(filepath)
        except socket.error, e:     
            if isinstance(e.args, tuple):
                if e[0] in (errno.EPIPE, errno.ECONNRESET):
                   print "disconnected"
                   self.suppress_socket_error_report = True
                   return
            
            raise


    def handle_one_request(self):
        try:
            return BaseHTTPServer.BaseHTTPRequestHandler.handle_one_request(self)
        except socket.error:
            if not self.suppress_socket_error_report:
                raise


    def finish(self):
        try:
            return BaseHTTPServer.BaseHTTPRequestHandler.finish(self)
        except socket.error:
            if not self.suppress_socket_error_report:
                raise


    def send_headers(self, filepath):
        self.protocol_version = "HTTP/1.1"
        self.send_response(200)
        self.send_header("Content-type", self.content_type)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()    


    def write_response(self, filepath):
        with open(filepath, "rb") as f:           
            while True:
                line = f.read(1024)
                if len(line) == 0:
                    break
            
                chunk_size = "%0.2X" % len(line)
                self.wfile.write(chunk_size)
                self.wfile.write("\r\n")
                self.wfile.write(line) 
                self.wfile.write("\r\n")  
                
        self.wfile.write("0")
        self.wfile.write("\r\n\r\n")                             



class TranscodingRequestHandler(RequestHandler):
    """ Handle HTTP requests for files which require realtime transcoding with ffmpeg """
    transcoder_command = FFMPEG
    transcode_options = ""
    bufsize = 0
                    
    def write_response(self, filepath):
        if self.bufsize != 0:
            print "transcode buffer size:", self.bufsize
        
        ffmpeg_command = self.transcoder_command % (filepath, self.transcode_options) 
        
        ffmpeg_process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, shell=True, bufsize=self.bufsize)       

        for line in ffmpeg_process.stdout:
            chunk_size = "%0.2X" % len(line)
            self.wfile.write(chunk_size)
            self.wfile.write("\r\n")
            self.wfile.write(line) 
            self.wfile.write("\r\n")            
            
        self.wfile.write("0")
        self.wfile.write("\r\n\r\n")                         


            
def get_transcoder_cmds(preferred_transcoder=None):
    """ establish which transcoder utility to use depending on what is installed """
    probe_cmd = None
    transcoder_cmd = None
    
    ffmpeg_installed = is_transcoder_installed("ffmpeg")
    avconv_installed = is_transcoder_installed("avconv")  
    
    # if anything other than avconv is preferred, try to use ffmpeg otherwise use avconv    
    if preferred_transcoder != "avconv":
        if ffmpeg_installed:
            transcoder_cmd = "ffmpeg"
            probe_cmd = "ffprobe"
        elif avconv_installed:
            print "unable to find ffmpeg - using avconv"
            transcoder_cmd = "avconv"
            probe_cmd = "avprobe"
    
    # otherwise, avconv is preferred, so try to use avconv, followed by ffmpeg  
    else:
        if avconv_installed:
            transcoder_cmd = "avconv"
            probe_cmd = "avprobe"
        elif ffmpeg_installed:
            print "unable to find avconv - using ffmpeg"
            transcoder_cmd = "ffmpeg"
            probe_cmd = "ffprobe"
            
    return transcoder_cmd, probe_cmd
    
    
                

def is_transcoder_installed(transcoder_application):
    """ check for an installation of either ffmpeg or avconv """
    try:
        subprocess.check_output([transcoder_application, "-version"])
        return True
    except OSError:
        return False
       



def kill_old_pid(device_ip):
    """ attempts to kill a previously running instance of this application casting to the specified device. """
    pid_file = PIDFILE % device_ip
    try:
        with open(pid_file, "r") as pidfile:
            pid = int(pidfile.read())
            os.killpg(pid, signal.SIGTERM)    
    except:
        pass
               


def save_pid(device_ip):
    """ saves the process id of this application casting to the specified device in a pid file. """
    pid_file = PIDFILE % device_ip
    with open(pid_file, "w") as pidfile:
        pidfile.write("%d" %  os.getpid())




def get_mimetype(filename, ffprobe_cmd=None):
    """ find the container format of the file """
    # default value
    mimetype = "video/mp4"
    
    
    # guess based on filename extension
    guess = mimetypes.guess_type(filename)[0]
    if guess is not None:
        if guess.lower().startswith("video/") or guess.lower().startswith("audio/"):
            mimetype = guess
      
        
    # use the OS file command...
    try:
        file_cmd = 'file --mime-type -b "%s"' % filename
        file_mimetype = subprocess.check_output(file_cmd, shell=True).strip().lower()
        
        if file_mimetype.startswith("video/") or file_mimetype.startswith("audio/"):
            mimetype = file_mimetype
            
            print "OS identifies the mimetype as :", mimetype
            return mimetype
    except:
        pass
    
    
    # use ffmpeg/avconv if installed
    if ffprobe_cmd is None:
        return mimetype
    
    # ffmpeg/avconv is installed
    has_video = False
    has_audio = False
    format_name = None
    
    ffprobe_cmd = '%s -show_streams -show_format "%s"' % (ffprobe_cmd, filename)
    ffmpeg_process = subprocess.Popen(ffprobe_cmd, stdout=subprocess.PIPE, shell=True)

    for line in ffmpeg_process.stdout:
        if line.startswith("codec_type=audio"):
            has_audio = True
        elif line.startswith("codec_type=video"):
            has_video = True    
        elif line.startswith("format_name="):
            name, value = line.split("=")
            format_name = value.strip().lower().split(",")


    # use the default if it isn't possible to identify the format type
    if format_name is None:
        return mimetype
    
    
    if has_video:
        mimetype = "video/"
    else:
        mimetype = "audio/"
        
    if "mp4" in format_name:
        mimetype += "mp4"            
    elif "webm" in format_name:
        mimetype += "webm"
    elif "ogg" in format_name:
        mimetype += "ogg"        
    elif "mp3" in format_name:
        mimetype = "audio/mpeg"
    elif "wav" in format_name:
        mimetype = "audio/wav" 
    else:   
        mimetype += "mp4"     
        
    return mimetype
    
            
            
def play(filename, transcode, transcoder, transcode_options, transcode_bufsize, device_name, server_port):
    """ play a local file on the chromecast """

    print_ident()

    if os.path.isfile(filename):
        filename = os.path.abspath(filename)
    else:
        sys.exit("media file %s not found" % filename)
        

    cast = CCMediaController(device_name=device_name)
    
    kill_old_pid(cast.host)
    save_pid(cast.host)
        
    print "Playing:", filename
    
    transcoder_cmd, probe_cmd = get_transcoder_cmds(preferred_transcoder=transcoder)
        
    mimetype = get_mimetype(filename, probe_cmd)

    status = cast.get_status()
    webserver_ip = status['client'][0]
    
    print "my ip address:", webserver_ip
        
    
    req_handler = RequestHandler
    
    if transcode:
        if transcoder_cmd in ("ffmpeg", "avconv"):
            req_handler = TranscodingRequestHandler
            
            if transcoder_cmd == "ffmpeg":  
                req_handler.transcoder_command = FFMPEG
            else:
                req_handler.transcoder_command = AVCONV
                
            if transcode_options is not None:    
                req_handler.transcode_options = transcode_options
                
            req_handler.bufsize = transcode_bufsize
        else:
            print "No transcoder is installed. Attempting standard playback"
            req_handler.content_type = mimetype    
    else:
        req_handler.content_type = mimetype    
        
    
    # create a webserver to handle a single request on a free port or a specific port if passed in the parameter   
    port = 0    
    
    if server_port is not None:
        port = int(server_port)
        
    server = BaseHTTPServer.HTTPServer((webserver_ip, port), req_handler)
    
    thread = Thread(target=server.handle_request)
    thread.start()    

    
    url = "http://%s:%s%s" % (webserver_ip, str(server.server_port), urllib.quote_plus(filename, "/"))
    print "URL & content-type: ", url, req_handler.content_type

    load(cast, url, req_handler.content_type)

    
    

def load(cast, url, mimetype):
    """ load a chromecast instance with a url and wait for idle state """
    try:
        print "loading media..."
        
        cast.load(url, mimetype)
        
        # wait for playback to complete before exiting
        print "waiting for player to finish - press ctrl-c to stop..."    
        
        idle = False
        while not idle:
            time.sleep(1)
            idle = cast.is_idle()
   
    except KeyboardInterrupt:
        print
        print "stopping..."
        cast.stop()
        
    finally:
        print "done"
    
    
def playurl(url, device_name):
    """ play a remote HTTP resource on the chromecast """
    
    print_ident()

    url_parsed = urlparse.urlparse(url)
    
    scheme = url_parsed.scheme
    host = url_parsed.netloc
    path = url.split(host, 1)[-1]
    
    conn = None
    if scheme == "https":
        conn = httplib.HTTPSConnection(host)
    else:
        conn = httplib.HTTPConnection(host)
        
    conn.request("HEAD", path)
    
    resp = conn.getresponse()
    
    if resp.status != 200:
        sys.exit("HTTP error:" + resp.status + " - " + resp.reason)
        
    print "Found HTTP resource"
    
    headers = resp.getheaders()
    
    mimetype = None
    
    for header in headers:
        if len(header) > 1:
            if header[0].lower() == "content-type":
                mimetype = header[1]
    
    if mimetype != None:            
        print "content-type:", mimetype
    else:
        mimetype = "video/mp4"
        print "resource does not specify mimetype - using default:", mimetype
    
    cast = CCMediaController(device_name=device_name)
    load(cast, url, mimetype)
    

            
    
def pause(device_name):
    """ pause playback """
    CCMediaController(device_name=device_name).pause()


def unpause(device_name):
    """ continue playback """
    CCMediaController(device_name=device_name).play()

        
def stop(device_name):
    """ stop playback and quit the media player app on the chromecast """
    CCMediaController(device_name=device_name).stop()


def get_status(device_name):
    """ print the status of the chromecast device """
    print CCMediaController(device_name=device_name).get_status()

def volume_up(device_name):
    """ raise the volume by 0.1 """
    CCMediaController(device_name=device_name).set_volume_up()


def volume_down(device_name):
    """ lower the volume by 0.1 """
    CCMediaController(device_name=device_name).set_volume_down()


def set_volume(device_name, volume):
    """ set the volume to level between 0 and 1 """
    CCMediaController(device_name=device_name).set_volume(volume)
    
    
def list_devices():
    print "Searching for devices, please wait..."
    device_ips = cc_device_finder.search_network(device_limit=None, time_limit=10)
    
    print "%d devices found" % len(device_ips)
    
    for device_ip in device_ips:
        print device_ip, ":", cc_device_finder.get_device_name(device_ip)
        

def print_ident():
    """ display initial messages """
    print
    print "-----------------------------------------"   
    print     
    print "Stream2Chromecast version:%s" % VERSION        
    print 
    print "Copyright (C) 2014-2016 Pat Carter"
    print "GNU General Public License v3.0" 
    print "https://www.gnu.org/licenses/gpl-3.0.html"
    print    
    print "-----------------------------------------"
    print


def parse_args():
    device_parser = argparse.ArgumentParser(add_help=False)
    device_group = device_parser.add_argument_group("device")
    device_group.add_argument("-d", "--device_name",
                              help="specify an Chromecast device by name (or ip address) explicitly: "
                                   "e.g. to play a file on a specific device", default=None)

    server_parser = argparse.ArgumentParser(add_help=False)
    server_group = server_parser.add_argument_group("server")
    server_group.add_argument("-p", "--server_port",
                              help="specify the port from which the media is streamed. "
                                   "This can be useful in a firewalled environment", default=None)

    transcoder_parser = argparse.ArgumentParser(add_help=False)
    transcoder_group = transcoder_parser.add_argument_group("transcoder")
    transcoder_group.add_argument("--transcode", action="store_true",
                                  help="Play an unsupported media type (e.g. an mpg file) "
                                        "using ffmpeg or avconv as a realtime transcoder "
                                       "(requires ffmpeg or avconv to be installed)")
    transcoder_group.add_argument("--transcoder", choices=["ffmpeg", "avconv"], default="ffmpeg")
    transcoder_group.add_argument("--transcode_options",
                                  help="option to supply custom parameters to the "
                                       "transcoder (ffmpeg or avconv)", default=None)
    transcoder_group.add_argument("--transcode_bufsize", type=int,
                                  help="pecify the buffer size of the data returned from the transcoder. "
                                       "Increasing this can help when on a slow network.", default=0)


    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()

    devices_list_parser = subparsers.add_parser("devices_list",
                                                help="Search for all Chromecast devices on the network")
    devices_list_parser.set_defaults(function=list_devices)

    play_parser = subparsers.add_parser("play", parents=[device_parser, server_parser, transcoder_parser],
                                        help= "Play a file")
    play_parser.add_argument("filename", help="The file to play")
    play_parser.set_defaults(function=play)

    play_url = subparsers.add_parser("playurl", parents=[device_parser],
                                     help="Play remote file using a URL (e.g. a web video)")
    play_url.add_argument("url", help="The url to play")
    play_url.set_defaults(function=playurl)

    pause_parser = subparsers.add_parser("pause", parents=[device_parser],
                                         help="Pause the current file playing")
    pause_parser.set_defaults(function=pause)

    continue_parser = subparsers.add_parser("continue", parents=[device_parser],
                                            help="Continue (Unpause) the current file playing")
    continue_parser.set_defaults(function=unpause)

    stop_parser = subparsers.add_parser("stop", parents=[device_parser],
                                        help="Stop the current file playing")
    stop_parser.set_defaults(function=stop)

    status_parser = subparsers.add_parser("status", parents=[device_parser],
                                          help="Display Chromecast status")
    status_parser.set_defaults(function=get_status)

    setvol_parser = subparsers.add_parser("setvol", parents=[device_parser],
                                          help="Set the volume")
    setvol_parser.add_argument("volume", type=float, help="value between 0 & 1.0  (e.g. 0.5 = half volume)")
    setvol_parser.set_defaults(function=set_volume)

    volume_up_parser = subparsers.add_parser("volup", parents=[device_parser],
                                             help="Increase volume by 0.1")
    volume_up_parser.set_defaults(function=volume_up)

    volume_down_parser = subparsers.add_parser("voldown", parents=[device_parser],
                                               help="Decrease volume by 0.1")
    volume_down_parser.set_defaults(function=volume_down)

    mute_parser = subparsers.add_parser("mute", parents=[device_parser],
                                        help="Mute the volume")
    mute_parser.set_defaults(volume=0)
    mute_parser.set_defaults(function=set_volume)

    return parser.parse_args()

def run():
    args = parse_args()
    args_dict = vars(args)
    func = args_dict.pop("function")
    func(**args_dict)
        
            
if __name__ == "__main__":
    run()
