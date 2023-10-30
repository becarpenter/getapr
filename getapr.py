"""Get Address Pairs (getapr)

This module provides getapr.get_addr_pairs(), which is intended
to be used instead of socket.getaddrinfo(). Instead of returning
a list of destination addresses, it returns a list of source and
destination address pairs. This mitigates the problem of the
operating system choosing an inappropriate source address.
Nevertheless, upper layer code needs to cycle through the list
of address pairs until it makes a successful connection.

The module also provides getapr.init_getapr() which initialises
the state information and asynchronous processes used by
get_addr_pairs(). This initialisation will take at least
10 seconds and includes network probes. If the user does not
call this function, it will be called automatically by the
first call to get_addr_pairs().

The module also provides getapr.status() which returns a
Python dictionary indicating the detected connectivity
status. For example, getapr.status()['NPTv6'] is a Boolean
indicating whether an NPTv6 or NAT66 translator is present.

This code is a prototype. It does not cover all possible
complications. It uses two randomly chosen Atlas probes
as initial probe targets. IPv6 is always preferred if
available. A rolling average latency is recorded and used
for sorting results. Some features are missing so far:

1) Source addresses should be ignored if they stop working,
to mitigate multihoming outages.

2) The probe targets should be refreshed periodically, to
spread load.

The prototype was  tested on Windows 10 and Linux 5.4.0,
and it needs at least Python 3.9.

Note for programmers: The handling of interface (a.k.a. scope
or zone) identifiers is very different between the Windows
and POSIX socket APIs. The code attempts to handle link local
addresses consistently despite these differences, but there
may be glitches.
"""

########################################################
# Released under the BSD "Revised" License as follows:
#                                                     
# Copyright (C) 2023 Brian E. Carpenter.                  
# All rights reserved.
#
# Redistribution and use in source and binary forms, with
# or without modification, are permitted provided that the
# following conditions are met:
#
# 1. Redistributions of source code must retain the above
# copyright notice, this list of conditions and the following
# disclaimer.
#
# 2. Redistributions in binary form must reproduce the above
# copyright notice, this list of conditions and the following
# disclaimer in the documentation and/or other materials
# provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of
# its contributors may be used to endorse or promote products
# derived from this software without specific prior written
# permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS  
# AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED 
# WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A     
# PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL
# THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF
# USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)    
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER
# IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING   
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
# USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE        
# POSSIBILITY OF SUCH DAMAGE.                         
#                                                     
########################################################

# 20231002 first version
# 20231030 latency measurement and sorting, add new
#          destinations automatically


import os
import time
import socket
import ipaddress
import threading
import subprocess
import binascii
import random
import copy

####################################################
# import Atlas probe API                           #
####################################################

try:
    from ripe.atlas.cousteau import Probe
except:
    print("Could not import Probe",
        "\nPlease install ripe.atlas.cousteau with pip or apt-get.")
    time.sleep(10)
    exit()


####################################################
# o/s check and POSIX import                       #
####################################################

#Note that netifaces is supposedly available for
#Windows but it requires baroque installation
#mechanisms, so the code avoids it. On POSIX systems
#it simplifies life, but only since Python 3.9
#when the handling of Zone-ID was done properly

if os.name!="nt":
    import sys
    _version = sys.version_info
    if _version[0] < 3 or _version[1] < 9:
        print("Need at least Python 3.9.")
        time.sleep(10)
        exit()
    try:
        import netifaces
    except:
        print("Could not import netifaces",
        "\nPlease install netifaces with pip or apt-get.")
        time.sleep(10)
        exit()
        
####################################################
# Class to hold an address pair & telemetry data   #
####################################################

class _addr_pair:
    """Address pair with properties"""
    def __init__(self, sa, da, latency):
        self.sa = sa  #source address (as ipaddress.ip_address)
        self.da = da  #destination address (as ipaddress.ip_address)
        self.latency = latency  # latency (ms)
    def __repr__(self):
        return repr((self.sa, self.da, self.latency))

        

####################################################
# Global initialisations                           #
####################################################

_prng = random.SystemRandom()
_sa_list_lock = threading.Lock()
_sa_list = []   #list of possible source addresses
_da_list_lock = threading.Lock()
_da_list = []   #list of destination addresses to test
_pair_list_lock = threading.Lock()
_pair_list = [] #list of successful address pairs with latency

_poll_count = 0 #keep track of polling
_max_da = 10    #how big we allow the destination list to grow

_test_done = False  #used for a one-time-through test mode
_logging = True     #set when selective logging wanted
_printing = False   #set if log printing wanted
_getapr_initialised = False

def_gateway4 = None #default gateways
def_gateway6 = None

_timeout = 5        #timeout for connect attempts (s)
_latency6 = 200     #default latency for IPv6 (ms)
_latency4 = 250     #default latency for IPv4 (ms)
    
NPTv6 = False       #NPTv6 or NAPT66 assumed absent by default
NAT44 = False       #NAPT44 assumed absent by default
NPTv6_tried = False #To detect first time through
NAT44_tried = False #To detect first time through
ULA_present = False #ULA assumed absent by default
RFC1918 = False     #RFC1918 assumed absent by default
ULA_ok = False      #Turns True on first ULA<>ULA success
GUA_ok = False      #Turns True on first GUA<>GUA success
LLA_ok = False      #Turns True on first LLA<>LLA success
IPv4_ok = False     #Turns True on first IPv4<>IPv4 success


def _log(*whatever):
    """Print, if wanted"""
    if _printing:
        _s=""
        for x in whatever:          
            try:               
                _s=_s+str(x)+" "
                print(x,end=" ",flush=False)
            except:
                #in case UTF-8 string (or something else) can't be printed
                print("[unprintable]",end="",flush=False)
        #_s could be added to a log file
        print("")

def _log_lists():
    """Print lists, if wanted"""
    _log("\nSources:")
    for _a in _sa_list:
        _log(_a)
    _log("\nDestinations:")
    for _a in _da_list:
        _log(_a)
            


def _is_ula(a):
    """Test for ULA"""
    return (str(a).startswith('fd') or str(a).startswith('fc'))

def _update_sources():
    """Find current available source addresses"""
####################################################
# This code is very o/s dependent
####################################################
    global _sa_list, _sa_list_lock, ULA_present, RFC1918, def_gateway4, def_gateway6
    _sa_list_lock.acquire()
    _sa_list = []   # Empty list of source addresses
    if os.name=="nt":
        #This only works on Windows              
        _addrinfo = socket.getaddrinfo(socket.gethostname(),0)
        for _af,_temp1,_temp2,_temp3,_addr in _addrinfo:
            if _af == socket.AF_INET6:
                _addr,_temp,_temp,_zid = _addr  #get first item from tuple
                _loc = ipaddress.IPv6Address(_addr)
                if _loc.is_loopback:
                    continue
                if (not '%' in _addr) and _loc.is_link_local:
                    #this applies on Windows for Python 3.7 upwards
                    _addr += "%"+str(_zid)
                    _loc = ipaddress.IPv6Address(_addr)
                if _is_ula(_loc):
                    ULA_present = True
                _sa_list.append(_loc)
                    
            elif _af == socket.AF_INET:
                _addr,_temp = _addr  #get first item from tuple
                _loc = ipaddress.IPv4Address(_addr)
                if _loc.is_loopback:
                    continue
                if _loc.is_private:
                    RFC1918 = True
                _sa_list.append(_loc)
        #Get default gateways
        _ing = False
        for l in os.popen("ipconfig"):
            _s = str(l)
            if _ing:
                if _s.startswith("                                      "):
                   def_gateway4 = ipaddress.ip_address(_s.strip())
                _ing = False
            elif "Default Gateway" in _s:
                _ing = True
                def_gateway6 = ipaddress.ip_address(_s.split(" ")[-1].strip())           
    else:
        # Assume POSIX
        ifs = netifaces.interfaces()
        for interface in ifs:
            config = netifaces.ifaddresses(interface)
            if netifaces.AF_INET6 in config.keys():
                for link in config[netifaces.AF_INET6]:
                    if 'addr' in link.keys():
                        _addr = link['addr']
                        _loc = ipaddress.IPv6Address(_addr)
                        if _loc.is_loopback:
                            continue
                        if _is_ula(_loc):
                            ULA_present = True
                        _sa_list.append(_loc)
            if netifaces.AF_INET in config.keys():
                for link in config[netifaces.AF_INET]:
                    if 'addr' in link.keys():
                        _addr = link['addr']
                        _loc = ipaddress.IPv4Address(_addr)
                        if _loc.is_loopback:
                            continue
                        if _loc.is_private:
                            RFC1918 = True
                        _sa_list.append(_loc)
        # Get default gateways
        gateways = netifaces.gateways()
        try:
            def_gateway4 = ipaddress.IPv4Address(gateways['default'][netifaces.AF_INET][0])
        except:
            pass
        try:
            _gwa = gateways['default'][netifaces.AF_INET6][0]
            _zid = gateways['default'][netifaces.AF_INET6][1]
            def_gateway6 = ipaddress.IPv6Address(_gwa+"%"+_zid)        
        except:
            pass
                        
    _sa_list_lock.release()


def _ok(sa, da):
    """Check address pair. Return False if bad, latency in ms if OK"""

    global NPTv6, NAT44, NPTv6_tried, NAT44_tried, ULA_ok, GUA_ok, LLA_ok, IPv4_ok
    
    if sa.version != da.version:
        return(False)   #never try NAT46 or NAT64
    if sa.is_link_local != da.is_link_local:
        return(False)   #link-locals can only talk to each other
    try:
        if sa.version == 6:
            if sa.is_link_local and sa.scope_id != da.scope_id:
                #print("!scope", sa.scope_id, da.scope_id)
                return(False)   #different interface
            zid = 0
            if sa.is_link_local and da.is_link_local:
                #print("!2 LLAs", sa, da)
                #must split interface index off because Linux is fussy ... but not for Windows
                if os.name != "nt":
                    sa,zid = str(sa).split("%")
                    sa = ipaddress.IPv6Address(sa)
                    zid = socket.if_nametoindex(zid) #convert to numeric
                    da,_ = str(da).split("%")
                    da = ipaddress.IPv6Address(da)
                    #print("!LLA", sa, da, zid)
            
            if _is_ula(sa) and not _is_ula(da):
                if NPTv6_tried and not NPTv6:
                    return(False)   #ULAs can only talk to each other
                else:
                    NPTv6_tried = True
            sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            sock.bind((str(sa), 0, 0, zid))
            sock.settimeout(_timeout)
            t0 = time.monotonic()
            sock.connect((str(da), 80, 0, zid))
            latency = max(int((time.monotonic() - t0)*1000),1)
            if _is_ula(sa) and not _is_ula(da):
                NPTv6 = True
            elif _is_ula(sa) and _is_ula(da):
                ULA_ok = True
            elif sa.is_link_local and da.is_link_local:
                LLA_ok = True
            else:
                GUA_ok = True                    
        else:
            if sa.is_private and not da.is_private:
                if NAT44_tried and not NAT44:
                    return(False)   #RFC1918s can only talk to each other
                else:
                    NAT44_tried = True          
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind((str(sa),0))
            sock.settimeout(_timeout)
            t0 = time.monotonic()
            sock.connect((str(da), 80))
            latency = max(int((time.monotonic() - t0)*1000),1)
            IPv4_ok = True
            if sa.is_private and not da.is_private:
                NAT44 = True
        sock.close()
    except Exception as ex:
        #print("!connect", ex, sa, da)
        return(False)
    return(latency)

def _in_pair_list(sa, da, remove = False, latency = False):
    """Utility function for _poll"""
    #called with pair list locked!
    global _pair_list
    for pr in _pair_list:
        if sa == pr.sa and da == pr.da:
            if remove:
                _pair_list.remove(pr)
            elif latency:
                #fresh data, update rolling average
                pr.latency = int((pr.latency + latency)/2)                 
            return(pr)
    return False

    
class _poll(threading.Thread):
    """Poll SA/DA pairs"""
####################################################
# This thread polls {SA, DA} pairs to see what     #
# works and what doesn't.                          #
# It grabs temporary copies of lists to reduce     #
# waiting time for user calls.                     #
####################################################

    def __init__(self):
        threading.Thread.__init__(self, daemon=True)
                
    def run(self):
        global _sa_list, _da_list, _pair_list, _poll_count
        while True:    
            _sa_list_lock.acquire()
            sa_list = copy.copy(_sa_list)
            _sa_list_lock.release()
            for sa in sa_list:
                remove_da_list = []
                _da_list_lock.acquire()
                da_list = copy.copy(_da_list)
                _da_list_lock.release()
                for da in da_list:
                    #print("Polling",sa,_a)
                    latency = _ok(sa, da) #NB this call can take 5 seconds to timeout
                    if latency: 
                        #print("Poll OK")
                        _pair_list_lock.acquire()
                        if not _in_pair_list(sa, da, latency = latency):
                            _pair_list.append(_addr_pair(sa, da, latency))
                        _pair_list_lock.release()
                    else:
                        #print("Poll failed", sa, da)
                        _pair_list_lock.acquire()
                        _in_pair_list(sa, da, remove = True)
                        _pair_list_lock.release()

                        #Should it have worked, according to flags?
                        #If so, remove destination to avoid future timeouts.
                        #print("Failed:", sa, _da)
                        if sa.version == 4 and da.version == 4:
                            if sa.is_private and da.is_global and NAT44:
                                remove_da_list.append(da)
                            elif sa.is_global and da.is_global and IPv4_ok:
                                remove_da_list.append(da)
                        elif sa.version == 6 and da.version == 6:
                            if sa.is_link_local and da.is_link_local and LLA_ok and sa.scope_id == da.scope_id:
                                remove_da_list.append(da)
                            elif _is_ula(sa) and _is_ula(da) and ULA_ok:
                                remove_da_list.append(da)
                            elif _is_ula(sa)and da.is_global and NPTv6 :
                                remove_da_list.append(da)
                            elif sa.is_global and da.is_global and GUA_ok:
                                remove_da_list.append(da)
                            
                    
                if remove_da_list:
                    #print("Removing destinations", remove_da_list)
                    _da_list_lock.acquire()
                    for  da in remove_da_list:
                        if da in _da_list:
                            _da_list.remove(da)
                    _da_list_lock.release()
                    
            _poll_count += 1
            if _poll_count > 1000:
                _poll_count = 0
        
            time.sleep(10)

class _monitor(threading.Thread):
    """Monitor progress"""
####################################################
# This thread monitors and logs progress           #
####################################################

    def __init__(self):
        threading.Thread.__init__(self, daemon=True)
                
    def run(self):
        global _sa_list, _da_list, _pair_list, _poll_count
        global _sa_list_lock, _da_list_lock, _pair_list_lock
        global _logging, _test_done
        

        while True:
            time.sleep(10)
            if _logging:
                if _poll_count > 1:
                    _log_lists()
                _pair_list_lock.acquire()
                _log("\nPair list:")
                for _a in _pair_list:
                    _log(str(_a.sa) +";"+ str(_a.da) +";"+ str(_a.latency))
                _pair_list_lock.release()

                _log("\nStatus:")
                _log("GUA<>GUA:", GUA_ok, ", ULA<>ULA:", ULA_ok, ", LLA<>LLA:", LLA_ok, ", IPv4<>IPv4:", IPv4_ok)
                _log("ULA:", ULA_present,", NPTv6:", NPTv6, ", RFC1918:", RFC1918, ", NAT44:", NAT44)
                _log("Poll count:", _poll_count)
            _logging = False

            #Local hacks to test ULA testing...
            
##            if _poll_count >= 1 and not _test_done:
##                _da_list_lock.acquire()
##                _da_list += [ipaddress.IPv6Address("fd63:45eb:dc14:0:2e3a:fdff:fea4:dde7")]
##                                                    #replace with a locally valid ULA
##                #print("added dest", _da_list[-1])
##
####                #...and destination list purging.
####                #If you uncomment this, there will be long delays
####                #while pointlessly probing these addresses.
####                _da_list += [ipaddress.IPv6Address("2001:db8:abcd:0101::abc1"),
####                             ipaddress.IPv6Address("2001:db8:b123:0101::def2"),
####                             ipaddress.IPv6Address("2001:db8:abcd:0101::abc2"),
####                             ipaddress.IPv6Address("2001:db8:b123:0101::def3"),
####                             ipaddress.IPv6Address("2001:db8:abcd:0101::abc3"),
####                             ipaddress.IPv6Address("2001:db8:b123:0101::def4"),
####                             ipaddress.IPv6Address("2001:db8:abcd:0101::abc4"),
####                             ipaddress.IPv6Address("2001:db8:b123:0101::def5"),
####                             ipaddress.IPv6Address("2001:db8:abcd:0101::abc5"),
####                             ipaddress.IPv6Address("2001:db8:b123:0101::def6"),
####                             ipaddress.IPv6Address("2001:db8:abcd:0101::abc6"),
####                             ipaddress.IPv6Address("2001:db8:b123:0101::def7"),
####                             ipaddress.IPv6Address("2001:db8:abcd:0101::abc7"),
####                             ipaddress.IPv6Address("2001:db8:b123:0101::def8")]
##
##                _da_list_lock.release()
##                _test_done = True

            if not _poll_count%6:
                #regenerate source list
                _update_sources()
                #trim oldest entries in destination list
                _da_list_lock.acquire()
                while len(_da_list) > _max_da:
                    for _da in _da_list:
                        if not _da in (target6, target4, def_gateway6, def_gateway4):
                            _da_list.remove(_da)
                            break
                _da_list_lock.release()                    
                

            if _poll_count < 3 or not _poll_count%10:
                _logging = True

def get_addr_pairs(target, port, printing = False):
    """Get source and destination address pairs for the target host.
The target is a domain name, or an IPv4 or IPv6 address string.
A list of (AF, SA, DA) 3-tuples is returned. The list is empty
if no address pair is found. The AF will be socket.AF_INET
or socket.AF_INET6 and can be passed directly to socket.socket().
The addresses are returned as tuples that can be passed directly to
socket.bind() and socket.connect(). For example,

    pairs = get_addr_pairs("www.example.com", 80)
    if pairs:
        AF, SA, DA = pairs[0]
        user_sock = socket.socket(AF, socket.SOCK_STREAM)
        user_sock.bind(SA)
        user_sock.connect(DA)

The port parameter is used only to build the appropriate DA tuple.

The user is strongly recommended to try the address pairs in sequence.
IPv6 addresses always come first if available.

The optional 'printing' parameter controls informational printing
and is intended for debugging."""
    
    global _da_list, _pair_list

    if not _getapr_initialised:
        init_getapr(printing = printing)
    
    reply = []
    das = []
    if target:        #we do not handle a null host

        try:
            das.append(ipaddress.ip_address(target))
            #the user supplied an address
        except:         
            try:
                ainf = socket.getaddrinfo(target, port)
            except Exception as ex:
                if 'getaddrinfo failed' in ex:
                    return(reply)   #NXDOMAIN, so return nothing
                else:
                    raise(ex)       #something else, so re-raise it

            #collate, ensuring IPv6 is always first
            for item in ainf:
                if item[0].name == 'AF_INET6':
                    das.append(ipaddress.ip_address(item[4][0]))
            for item in ainf:
                if item[0].name == 'AF_INET':
                    das.append(ipaddress.ip_address(item[4][0]))
                
        #process list of destinations (if any)
        for da in das:
            #is da already known?
            known_da = True
            _da_list_lock.acquire()
            if not da in _da_list:
                _da_list.append(da)
                known_da = False
            _da_list_lock.release()
            
            #grab a copy of the pair list
            _pair_list_lock.acquire()
            pl = copy.copy(_pair_list) #!deepcopy fails on LLA
            _pair_list_lock.release()

            this_da_found = False
            if known_da:
                #look for and suggest known pairs
                for pair in pl:
                    if pair.da == da:
                        reply.append(pair)
                        this_da_found = True
                       
            if not this_da_found:
                #not yet in destination list, so check against flags
                #and add if suitable
                
                #first, grab a copy of the source list
                _sa_list_lock.acquire()
                sl = copy.copy(_sa_list) #!deepcopy fails on LLA
                _sa_list_lock.release()

                useful = False
                if da.version == 6:
                    if da.is_global and GUA_ok:
                        #suggest GUA sources with default latency
                        for sa in sl:
                            if sa.version == 6 and sa.is_global:
                                reply.append(_addr_pair(sa, da, _latency6))
                                useful = True
                    if _is_ula(da):
                        #suggest ULA sources with tuned latency
                        for sa in sl:
                            if sa.version == 6 and _is_ula(sa):
                                reply.append(_addr_pair(sa, da, _latency6-1))
                                useful = True
                    if da.is_global and NPTv6:
                        #suggest ULA sources with translation and tuned latency
                        for sa in sl:
                            if sa.version == 6 and _is_ula(sa):
                                reply.append(_addr_pair(sa, da, _latency6+1))
                                useful = True
                    if da.is_link_local and LLA_ok:
                        #suggest LLA sources with minimal latency
                        for sa in sl:
                            if sa.version == 6 and sa.is_link_local and sa.scope_id == da.scope_id:
                                reply.append(_addr_pair(sa, da, 1)) 
                                useful = True
                if da.version == 4:
                    if (da.is_global and NAT44) or da.is_private:
                        #suggest RFC1918 sources with default latency
                        for sa in sl:
                            if sa.version == 4 and sa.is_private:
                                reply.append(_addr_pair(sa, da, _latency4))
                                useful = True
                    elif da.is_global and IPv4_ok:
                        #suggest global IPv4 sources with default latency
                        for sa in sl:
                            if sa.version == 4 and sa.is_global:
                                reply.append(_addr_pair(sa, da, _latency4))
                                useful = True
                    if da.is_link_local:
                        #suggest LLA sources with minimal latency +1
                        for sa in sl:
                            if sa.version == 4 and sa.is_link_local:
                                reply.append(_addr_pair(sa, da, 2)) 
                                useful = True
                if useful:
                    #add to destination list
                    _da_list_lock.acquire()
                    _da_list.append(da)
                    _da_list_lock.release()

    # sort replies on higher IP version and lower latency
    if reply:
        reply.sort(key = lambda p: (-p.sa.version, p.latency))

    # construct (AF, SA, DA) triples
    if reply:
        for i, pair in enumerate(reply):
            if pair.sa.version == 6:
                if pair.sa.is_link_local:    
                    #must split interface index off
                    da, zid = str(pair.da).split("%")
                    if os.name=="nt":
                        zid = eval(zid) #Windows: convert to numeric
                    else:
                        zid = socket.if_nametoindex(zid) #POSIX: convert to numeric                   
                else:
                    zid = 0
                    da = pair.da
                reply[i] = (socket.AF_INET6, (str(pair.sa),0,0,zid), (str(da), port, 0 ,zid))
            else:
                reply[i] = (socket.AF_INET, (str(pair.sa),0), (str(pair.da), port))
                
    return(reply)

def init_getapr(printing = False):
    """Initialise data and threads for source address detection and
destination probing.

The optional 'printing' parameter controls informational printing
and is intended for debugging.

Initialisation takes at least 10 seconds and includes network probes."""
    
    global _logging, _printing, _prng, target6, target4, def_gateway6,def_gateway4
    global _sa_list, _da_list, _pair_list, _poll_count, _getapr_initialised
    global _sa_list_lock, _da_list_lock, _pair_list_lock

    if _getapr_initialised:
        return

    _printing = printing

    #select a random pair of global probe targets
    #(ideally we'd repeat this every half hour to
    #spread the probes around the world)

    _log("Choosing probe targets; may take a minute...")
    target6 = None
    target4 = None
    for _i in range(1,10):
        _tryp = _prng.randint(6000, 7200)
        try:
            _probe = Probe(id=_tryp)
            if _probe.is_anchor and _probe.status == 'Connected' and _probe.address_v6:
                target6 = ipaddress.IPv6Address(_probe.address_v6)
                break
        except:
            pass
    for _i in range(1,10):
        _tryp = _prng.randint(6000, 7200)
        try:
            _probe = Probe(id=_tryp)
            if _probe.is_anchor and _probe.status == 'Connected' and _probe.address_v4:
                target4 = ipaddress.IPv4Address(_probe.address_v4)
                break
        except:
            pass

    #in case things are desparate...
    if not target6:
        target6 = ipaddress.IPv6Address("2a00:dd80:3c::b3f") #ipv6.lookup.test-ipv6.com
    if not target4:
        target4 = ipaddress.IPv4Address("216.218.223.250") #ipv4.lookup.test-ipv6.com
        
    _log("...chose", target6, "and", target4)
     
    _update_sources()

    _da_list_lock.acquire()
    _da_list.append(target6)
    _da_list.append(target4)
    if def_gateway6:
        _da_list.append(def_gateway6)
    if def_gateway4:
        _da_list.append(def_gateway4)
    _da_list_lock.release()

    _log_lists()
    _poll().start()
    _monitor().start()
    while _poll_count == 0:
        #wait until first poll complete
        time.sleep(1)
    _getapr_initialised = True

    # Return from init_getapr; 2 threads continue indefinitely

def status():
    """Returns dictionary showing detected connectivity status."""

    return({"GUA_ok": GUA_ok, "ULA_ok": ULA_ok, "LLA_ok": LLA_ok, "IPv4_ok": IPv4_ok,
     "ULA_present": ULA_present, "NPTv6": NPTv6, "RFC1918": RFC1918, "NAT44": NAT44})


                




    
