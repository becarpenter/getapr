# getapr
Get Address Pairs for socket programming in Python

~~~
Help on module getapr:

NAME
    getapr - Get Address Pairs (getapr)

DESCRIPTION
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
    as initial probe targets. Some features are missing so far:
    
    1) Source addresses should be ignored if they stop working,
    to mitigate multihoming outages.
    
    2) The probe targets should be refreshed periodically, to
    spread load.
    
    3) IPv6 is always preferred if available. No attempt is made
    to prefer shorter latency.
    
    The prototype was  tested on Windows 10 and Linux 5.4.0,
    and it needs at least Python 3.9.
    
    Note for programmers: The handling of interface (a.k.a. scope
    or zone) identifiers is very different between the Windows
    and POSIX socket APIs. The code attempts to handle link local
    addresses consistently despite these differences, but there
    may be glitches.

FUNCTIONS
    get_addr_pairs(target, port, printing=False)
        Get source and destination address pairs for the target host.
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
        and is intended for debugging.
    
    init_getapr(printing=False)
        Initialise data and threads for source address detection and
        destination probing.
        
        The optional 'printing' parameter controls informational printing
        and is intended for debugging.
        
        Initialisation takes at least 10 seconds and includes network probes.
    
    status()
        Returns dictionary showing detected connectivity status.

DATA
    GUA_ok = False
    IPv4_ok = False
    LLA_ok = False
    NAT44 = False
    NAT44_tried = False
    NPTv6 = False
    NPTv6_tried = False
    RFC1918 = False
    ULA_ok = False
    ULA_present = False
    def_gateway4 = None
    def_gateway6 = None
~~~