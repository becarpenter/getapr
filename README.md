# getapr
Get Address Pairs for socket programming in Python

(Also see the ProofOfConcept document.)

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

    The module also provided getapr.getaddrinfo() which works
    exactly like socket.getaddrinfo() except that it orders the
    destination addresses using get_addr_pairs(). In many cases,
    this will have the same effect as using get_addr_pairs().

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
    as initial probe targets. To spread load, new Atlas
    probes are picked every 10 minutes. The overall list
    of destination addresses is trimmed to a maximum size.

    A rolling average round-trip latency is recorded and used
    for sorting results. In the sort, IPv6 is preferred as long
    is is less than 5 msec slower than IPv4.

    New destination addresses are normally assigned a default
    starting latency. However, if a known destination matches
    the new one in the top 32 bits (IPv6) or 20 bits (IPv4), its
    existing latency is used for the new one, to reflect their
    probable topological closeness.

    The prototype was  tested on Windows 10 and 11, and Linux 5.4.0,
    and it needs at least Python 3.9 (tested up to 3.14).

    Note for programmers: The handling of interface (a.k.a. scope
    or zone) identifiers is very different between the Windows
    and POSIX socket APIs. The code attempts to handle link local
    addresses consistently despite these differences, but there
    may be glitches.

FUNCTIONS
    get_addr_pairs(target, port, printing=False)
        Get source and destination address pairs for the target host.
        The 'target' is a domain name, or an IPv4 or IPv6 address string.
        A list of (AF, SA, DA) 3-tuples is returned. The list is empty
        if no address pair is found.

        The user is strongly recommended to try the address pairs in sequence.
        There is a bias towards IPv6 addresses but otherwise the addresses
        are sorted in order of latency.

        The AF will be socket.AF_INET or socket.AF_INET6 and can be passed
        directly to socket.socket(). The addresses are returned as tuples
        that can be passed directly to socket.bind() and socket.connect().

        The 'port' parameter is used to build the appropriate DA tuples.

        The optional 'printing' parameter controls informational printing
        and is intended for debugging.

        A lazy usage would be:

            pairs = get_addr_pairs("www.example.com", 80)
            if pairs:
                AF, SA, DA = pairs[0] # use first result
                user_sock = socket.socket(AF, socket.SOCK_STREAM)
                user_sock.bind(SA)
                user_sock.connect(DA)
                # followed by socket operations
                user_sock.close()

        A better usage would be:

            pairs = get_addr_pairs("www.example.com", 80)
            for pair in pairs:
                try:
                    AF, SA, DA = pair # try results in order
                    user_sock = socket.socket(AF, socket.SOCK_STREAM)
                    user_sock.bind(SA)
                    user_sock.connect(DA)
                    # followed by socket operations
                    user_sock.close()
                    break
                except:
                    continue

    getaddrinfo(host, port, family=<AddressFamily.AF_UNSPEC: 0>,
                type=0, proto=0, flags=0)
        The same as socket.getaddrinfo() but returns answers ordered as per get_addr_pairs()

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