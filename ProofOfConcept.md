# Python Proof of Concept for Get Address Pairs

This section describes a proof-of-concept implementation of a function to provide a socket-level replacement for `getaddrinfo()` that, instead of returning a list of possible destination addresses, returns a list of possible (source, destination) address pairs. These pairs are ordered according to a specific policy, but the upper layer (whether it is a transport protocol or an application program) is expected to try the address pairs in sequence.

The implementation is coded in Python 3 and runs in user space. The module, named `getapr`, therefore has several limitations:

1. Relatively slow execution speed.

2. Only available to Python applications.

3. Information gleaned for one application cannot be re-used by another.

4. No access to kernel data within the IPv6 and IPv4 stacks.

## User interface

The user is provided with three functions:

1\. `get_addr_pairs(target, port)`, which is intended to be used instead of `getaddrinfo()`. Instead of returning a list of destination addresses, it returns a list of source and destination address pairs. In practice, it actually returns a list of (AF, SA, DA) 3-tuples, where the first parameter identifies the address family of the addresses. The addresses are returned as tuples that can be passed directly to `bind()` and `connect()`. For example,  

~~~       
    pairs = get_addr_pairs("www.example.com", 80)
    if pairs:
        AF, SA, DA = pairs[0]
        user_sock = socket.socket(AF, socket.SOCK_STREAM)
        user_sock.bind(SA)
        user_sock.connect(DA)
~~~

  Note that this code fragment automatically selects IPv4 or IPv6 via the AF value.  

  The port parameter is used only to build the appropriate DA tuple. The user is strongly recommended to try the address pairs in sequence (not shown in this example).
    
2\. `init_getapr()`, which initialises the state information and asynchronous processes used by    `get_addr_pairs()`. This initialisation takes at least 10 seconds including network probes. If the user does not call this function, it will be called automatically on the first call to `get_addr_pairs()`.
    
3\. `status()`, which returns a Python dictionary indicating the detected connectivity status. For example, the status element `NPTv6` is a Boolean indicating whether an NPTv6 (or NAT66) translator was detected.

The prototype was  tested on Windows 10 and Linux 5.4.0, and it needs at least Python 3.9.

See [github](https://github.com/becarpenter/getapr/) for more information and the code.

## Code description

There are some global data structures used throughout the code, protected by concurrency locks when necessary. The code includes two indefinitely running threads, `_poll` and `_monitor`, as well as the user-callable functions. 

### Data Structures

The most important data structures are:

 - `_sa_list`, a list of possible source addresses (SA). This is not static, see later.

 - `_da_list`, a list of destination addresses (DA) to test. This is not static, see later.

 - `_pair_list`, a dynamic list of successful address pairs with associated latency.

### Initialization

When the package is initialized (by calling `init_getapr()` or by the first call to `get_addr_pairs()`), the following actions occur:

1. Two probe targets, an IPv6 address and an IPv4 address, to be used as the basic targets for polling global IP access. The probes are currently chosen at random from the [RIPE ATLAS probe system](https://atlas.ripe.net/). Note that _data_ from ATLAS probes may not be used for commercial purposes without [permission](https://atlas.ripe.net/get-involved/commercial-use/), but `getapr` does not access such data. The addresses of these targets are loaded into `_da_list`, the list of destination addresses. They are used as validated global targets for the `_poll` thread.

2. Default gateways for IPv6 and IPv4 are determined and their addresses are loaded into `_da_list`. They are used as validated local targets for the `_poll` thread. (Very typically, they will be a LLA for IPv6 and an RFC1918 address for IPv4.)

3. The list of possible source addresses, `_sa_list`, is initialised using appropriate operating system functions.

4. An empty `_pair_list` is created.

5. The `_poll` and `_monitor` threads are started.

### Polling Thread

The role of `_poll` is to repeatedly poll (SA, DA) pairs to verify whether they work, i.e. whether it is in fact possible to successfully open a connection from SA to DA. Its main loop is repeated every ten seconds, plus waiting time when testing network connections.

The `_poll` thread loops across all possible (SA, DA) pairs from `_sa_list` and `_da_list`, discards those that are intrinsically impossible, and actively tests each pair that is theoretically possible. Currently the test is an attempted TCP connection on port 80. (Clearly, that could be improved.) The duration of a successful `connect()` call is recorded as the latency.

If a connection succeeds, the (SA, DA) pair is added to `_pair_list` (unless already present). The latency is used to maintain a running average latency for a successful pair. If a connection fails, the pair is removed from `_pair_list` (if present).

Additionally, if a connection succeeds, the result is used to set global variables as follows. For IPv6 address pairs:

~~~
    if (SA is ULA) and (DA is not ULA and is off-site):
        NPTv6 = True
    elif (SA is ULA) and (DA is ULA):
        ULA_ok = True
    elif (SA is link_local) and (DA is link_local):
        LLA_ok = True
    else:
        GUA_ok = True
~~~

For IPv4 address pairs:

~~~
    IPv4_ok = True
    if (SA is RFC1918) and (DA is not RFC1918):
        NAT44 = True
~~~

All these status Booleans are initially set to `False`. Therefore, `NPTv6`, `NAT44`, `LLA_ok`, `ULA_ok`, `GUA_ok`, and `IPv4_ok` will only show `True` if at least one connection requiring them has succeeded.

Thus, the purpose of `_poll` is to maintain an accurate `_pair_list` of successful (SA, DA) pairs and an accurate set of status Booleans.

### Monitor Thread

The main purposes of `_monitor` are:

1. Periodically refresh `_sa_list`, the list of possible source addresses, for example to delete addresses belonging to an interface that has gone down, or to add addresses for a newly eanabled interface. (In a kernel implementation, this could be done immediately instead of periodically.)

2. Periodically garbage-collect `_da_list`, by deleting the oldest ones (but not the ATLAS probes or the default gateways).

The `_monitor` thread also generates log output when logging is enabled. Its main loop is repeated every ten seconds.

### Get Address Pairs Function

The user of `get_addr_pairs()` may supply either an IP address or an FQDN. The function returns an ordered list of suggested source and destination address pairs, in a format easily used for standard socket calls. If the user provides an FQDN, the code uses `getaddrinfo()` to perform DNS lookup and build a list of destination addresses (DAs). If the user provides an IP address, the list will contain only that DA. 

In either case, for each listed DA, the code checks if it is in `_da_list`. 

 - If it is present, the code checks `_pair_list` and extracts all listed address pairs with this DA; these are added to the list to be returned to the caller of `get_addr_pairs()`. In this case, the user will receive a list of (SA, DA) pairs which have already been tested successfully.

 - If it is _not_ present, the DA is added to `_da_list`, so that future iterations of `_poll` will test it. Also, the code applies a series of rules in order to select suitable source addresses (SAs). For IPv6:

~~~
    if (DA is GUA) and GUA_ok:
        suggest all GUAs in _sa_list
    if (DA is ULA):
        suggest all ULAs in _sa_list
    if (DA is GUA) and NPTv6:
        suggest all ULAs in _sa_list
    if (DA is link_local) and LLA_ok:
        suggest all LLAs in _sa_list
~~~

For IPv4:

~~~
    if ((DA is global) and NAT44) or (DA is RFC1918):
        suggest all RFC1918 in _sa_list
    elif (DA is global) and IPv4_ok:
        suggest all IPv4 globals in _sa_list
    if (DA is link_local):
        suggest all IPv4 link-locals in _sa_list
~~~

Then, the user will receive the sequence of (SA, DA) pairs generated by the above process, sorted by higher IP version and lower latency. For the pairs selected by rule, a synthetic latency value is used as a tie breaker, as follows:

~~~
    GUA -> GUA:   200 ms
    ULA -> ULA:   199 ms
    ULA -> GUA:   201 ms
    LLA -> LLA:     1 ms
    IPv4 -> IPv4: 250 ms
    LLv4 -> LLv4:   2 ms
~~~

That can be read as a policy table, but it only takes effect in the absence of measured latency.

### Shortfalls
    
This code is a prototype and does not cover all possible complications. Some features are missing so far:

1. Source addresses should be ignored if they stop working, to mitigate unplanned outages.

2. The probe targets should be refreshed periodically, to spread load.

3. The only probe used is an attempted TCP connection on port 80.

4. GUAs are assumed to be off site - this is just lazy programming and should be fixed, at least by a heuristic based on a longest match.

5. Non-RFC1918 IPv4 addresses are assumed to be off site - fairly safe assumption but a bit lazy too. 

6. Longest-match checks should be applied to ULA pairs - more lazy programming.

7. There are no policy choices available.  IPv6 is always preferred if available.
    
Note for programmers: The handling of interface (a.k.a. scope or zone) identifiers is very different between the Windows and POSIX socket APIs. The code attempts to handle link local addresses consistently despite these differences, but there may be glitches.

