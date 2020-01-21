import logging
import random as rnd
import time

import scapy.layers.inet as inet
from scapy.layers.smb import *

import Attack.BaseAttack as BaseAttack
import Lib.SMB2 as SMB2
import Lib.SMBLib as SMBLib
import Lib.Utility as Util

from Attack.Parameter import Parameter, Boolean, Float, IntegerPositive, IPAddress, MACAddress, Percentage, Port,\
    SpecificString, String

logging.getLogger("scapy.runtime").setLevel(logging.ERROR)

# noinspection PyPep8


class SMBScanAttack(BaseAttack.BaseAttack):
    TARGET_COUNT = 'target.count'
    HOSTING_PERCENTAGE = 'hosting.percentage'
    PORT_SOURCE = 'port.src'
    IP_SOURCE_RANDOMIZE = 'ip.src.shuffle'
    PORT_SOURCE_RANDOMIZE = 'port.src.shuffle'
    HOSTING_IP = 'hosting.ip'
    HOSTING_VERSION = 'hosting.version'
    SOURCE_PLATFORM = 'src.platform'
    PROTOCOL_VERSION = 'protocol.version'

    def __init__(self):
        """
        Creates a new instance of the SMBScanAttack.
        This Attack injects TCP Syn Requests to the port 445 of several ips and related response into the output
        pcap file.
        If port 445 is open, it will simulate and inject the SMB Protocol Negotiation too.
        """
        # Initialize attack
        super(SMBScanAttack, self).__init__("SMBScan Attack", "Injects an SMB scan",
                                            "Scanning/Probing")

        self.host_os = Util.get_rnd_os()

        # Define allowed parameters and their type
        self.update_params([
            Parameter(self.IP_SOURCE, IPAddress()),
            Parameter(self.IP_DESTINATION, IPAddress()),
            Parameter(self.MAC_DESTINATION, MACAddress()),
            Parameter(self.TARGET_COUNT, IntegerPositive()),
            Parameter(self.HOSTING_PERCENTAGE, Percentage()),
            Parameter(self.PORT_SOURCE, Port()),
            Parameter(self.MAC_SOURCE, MACAddress()),
            Parameter(self.IP_SOURCE_RANDOMIZE, Boolean()),
            Parameter(self.PACKETS_PER_SECOND, Float()),
            Parameter(self.PORT_SOURCE_RANDOMIZE, Boolean()),
            Parameter(self.HOSTING_IP, IPAddress()),
            Parameter(self.HOSTING_VERSION, String()),
            Parameter(self.SOURCE_PLATFORM, SpecificString(Util.platforms)),
            Parameter(self.PROTOCOL_VERSION, String())
        ])

    def init_param(self, param: str) -> bool:
        """
        Initialize a parameter with its default values specified in this attack.

        :param param: parameter, which should be initialized
        :return: True if initialization was successful, False if not
        """
        value = None
        if param == self.IP_SOURCE:
            value = self.statistics.get_most_used_ip_address()
        elif param == self.IP_SOURCE_RANDOMIZE:
            value = 'False'
        elif param == self.MAC_SOURCE:
            ip_src = self.get_param_value(self.IP_SOURCE)
            if ip_src is None:
                return False
            value = self.get_mac_address(ip_src)
        elif param == self.TARGET_COUNT:
            value = 200
        elif param == self.IP_DESTINATION:
            value = "1.1.1.1"
        elif param == self.MAC_DESTINATION:
            ip_dst = self.get_param_value(self.IP_DESTINATION)
            if ip_dst is None:
                return False
            value = self.get_mac_address(ip_dst)
        elif param == self.PORT_SOURCE:
            value = rnd.randint(1024, 65535)
        elif param == self.PORT_SOURCE_RANDOMIZE:
            value = 'True'
        elif param == self.PACKETS_PER_SECOND:
            value = self.statistics.get_most_used_pps()
        elif param == self.INJECT_AFTER_PACKET:
            value = rnd.randint(0, self.statistics.get_packet_count())
        elif param == self.INJECT_AT_TIMESTAMP:
            value = self.get_intermediate_timestamp()
        elif param == self.HOSTING_PERCENTAGE:
            value = 0.5
        elif param == self.HOSTING_IP:
            value = "1.1.1.1"
        elif param == self.HOSTING_VERSION:
            value = SMBLib.get_smb_version(platform=self.host_os)
        elif param == self.SOURCE_PLATFORM:
            value = Util.get_rnd_os()
        elif param == self.PROTOCOL_VERSION:
            value = "1"
        if value is None:
            return False
        return self.add_param_value(param, value)

    def generate_attack_packets(self):
        """
        Creates the attack packets.
        """

        # Timestamp
        timestamp_next_pkt = self.get_param_value(self.INJECT_AT_TIMESTAMP)
        # store start time of attack
        self.attack_start_utime = timestamp_next_pkt
        timestamp_prv_reply, timestamp_confirm = 0, 0

        # Initialize parameters
        ip_source = self.get_param_value(self.IP_SOURCE)

        dest_ip_count = self.get_param_value(self.TARGET_COUNT)
        ip_addr_count = self.statistics.get_ip_address_count()
        if ip_addr_count < dest_ip_count + 1:
            dest_ip_count = ip_addr_count

        # Check for user defined target IP addresses
        ip_destinations = self.get_param_value(self.IP_DESTINATION)
        if isinstance(ip_destinations, list):
            dest_ip_count = dest_ip_count - len(ip_destinations)
        elif ip_destinations != "1.1.1.1":
            dest_ip_count = dest_ip_count - 1
            ip_destinations = [ip_destinations]
        else:
            ip_destinations = []

        # Take random targets from pcap
        rnd_ips = self.statistics.get_random_ip_address(dest_ip_count)
        if not isinstance(rnd_ips, list):
            rnd_ips = [rnd_ips]
        ip_destinations = ip_destinations + rnd_ips

        # Make sure the source IP is not part of targets
        if isinstance(ip_destinations, list) and ip_source in ip_destinations:
            ip_destinations.remove(ip_source)
        self.add_param_value(self.IP_DESTINATION, ip_destinations)

        # Calculate the amount of IP addresses which are hosting SMB
        host_percentage = self.get_param_value(self.HOSTING_PERCENTAGE)
        rnd_ip_count = len(ip_destinations) * host_percentage

        # Check for user defined IP addresses which are hosting SMB
        hosting_ip = self.get_param_value(self.HOSTING_IP)
        if isinstance(hosting_ip, list):
            rnd_ip_count = rnd_ip_count - len(hosting_ip)
        elif hosting_ip != "1.1.1.1":
            rnd_ip_count = rnd_ip_count - 1
            hosting_ip = [hosting_ip]
        else:
            hosting_ip = []

        hosting_ip = hosting_ip + ip_destinations[:int(rnd_ip_count)]
        self.add_param_value(self.HOSTING_IP, hosting_ip)

        # Shuffle targets
        rnd.shuffle(ip_destinations)

        # FIXME: Handle mac addresses correctly
        mac_source = self.get_param_value(self.MAC_SOURCE)
        mac_dest = self.get_param_value(self.MAC_DESTINATION)

        # Check smb version
        smb_version = self.get_param_value(self.PROTOCOL_VERSION)
        if smb_version not in SMBLib.smb_versions:
            SMBLib.invalid_smb_version(smb_version)
        hosting_version = self.get_param_value(self.HOSTING_VERSION)
        if hosting_version not in SMBLib.smb_versions:
            SMBLib.invalid_smb_version(hosting_version)
        # Check source platform
        src_platform = self.get_param_value(self.SOURCE_PLATFORM).lower()

        # randomize source ports according to platform, if specified
        if self.get_param_value(self.PORT_SOURCE_RANDOMIZE):
            sport = Util.generate_source_port_from_platform(src_platform)
        else:
            sport = self.get_param_value(self.PORT_SOURCE)

        # No destination IP was specified, but a destination MAC was specified, generate IP that fits MAC
        if isinstance(ip_destinations, list) and isinstance(mac_dest, str):
            ip_destinations = self.statistics.get_ip_address_from_mac(mac_dest)
            if len(ip_destinations) == 0:
                ip_destinations = self.generate_random_ipv4_address("Unknown", 1)
            # Check ip.src == ip.dst
            self.ip_src_dst_catch_equal(ip_source, ip_destinations)

        ip_dests = []
        if isinstance(ip_destinations, list):
            ip_dests = ip_destinations
        else:
            ip_dests.append(ip_destinations)

        if isinstance(ip_dests, list):
            rnd.shuffle(ip_dests)

        # Randomize source IP, if specified
        if self.get_param_value(self.IP_SOURCE_RANDOMIZE):
            ip_source = self.generate_random_ipv4_address("Unknown", 1)
            while ip_source in ip_dests:
                ip_source = self.generate_random_ipv4_address("Unknown", 1)
            mac_source = self.statistics.get_mac_address(str(ip_source))
            if len(mac_source) == 0:
                mac_source = self.generate_random_mac_address()

        # Get MSS, TTL and Window size value for source IP
        source_mss_value, source_ttl_value, source_win_value = self.get_ip_data(ip_source)

        mac_dests = self.statistics.get_mac_addresses(ip_dests)
        first_timestamp_smb = self.statistics.get_pcap_timestamp_start()[:19]

        for ip in ip_dests:

            if ip != ip_source:

                # Get destination Mac Address
                mac_destination = ""
                if ip in mac_dests.keys():
                    mac_destination = mac_dests[ip]
                if len(mac_destination) == 0:
                    if isinstance(mac_dest, str):
                        ip_from_mac = self.statistics.get_ip_address_from_mac(mac_dest)
                        if len(ip_from_mac) != 0:
                            ip = ip_from_mac
                            self.ip_src_dst_catch_equal(ip_source, ip)
                        mac_destination = mac_dest
                    else:
                        mac_destination = self.generate_random_mac_address()

                # Get MSS, TTL and Window size value for destination IP
                destination_mss_value, destination_ttl_value, destination_win_value = self.get_ip_data(ip)

                min_delay, max_delay = self.get_reply_latency(ip_source, ip)

                # New connection, new random TCP sequence numbers
                attacker_seq = rnd.randint(1000, 50000)
                victim_seq = rnd.randint(1000, 50000)

                # Randomize source port for each connection if specified
                if self.get_param_value(self.PORT_SOURCE_RANDOMIZE):
                    sport = Util.generate_source_port_from_platform(src_platform, sport)

                # 1) Build request package
                request_ether = inet.Ether(src=mac_source, dst=mac_destination)
                request_ip = inet.IP(src=ip_source, dst=ip, ttl=source_ttl_value, flags='DF')
                request_tcp = inet.TCP(sport=sport, dport=SMBLib.smb_port, window=source_win_value, flags='S',
                                       seq=attacker_seq, options=[('MSS', source_mss_value)])
                attacker_seq += 1
                request = (request_ether / request_ip / request_tcp)
                request.time = timestamp_next_pkt

                # Append request
                self.add_packet(request, ip_source, ip)

                # Update timestamp for next package
                timestamp_reply = self.timestamp_controller.next_timestamp(min_delay)

                if ip in hosting_ip:

                    # 2) Build TCP packages for ip that hosts SMB

                    # destination sends SYN, ACK
                    reply_ether = inet.Ether(src=mac_destination, dst=mac_source)
                    reply_ip = inet.IP(src=ip, dst=ip_source, ttl=destination_ttl_value, flags='DF')
                    reply_tcp = inet.TCP(sport=SMBLib.smb_port, dport=sport, seq=victim_seq, ack=attacker_seq,
                                         flags='SA',
                                         window=destination_win_value, options=[('MSS', destination_mss_value)])
                    victim_seq += 1
                    reply = (reply_ether / reply_ip / reply_tcp)
                    reply.time = timestamp_reply
                    self.add_packet(reply, ip_source, ip)

                    # requester confirms, ACK
                    confirm_ether = request_ether
                    confirm_ip = request_ip
                    confirm_tcp = inet.TCP(sport=sport, dport=SMBLib.smb_port, seq=attacker_seq, ack=victim_seq,
                                           window=source_win_value, flags='A')
                    confirm = (confirm_ether / confirm_ip / confirm_tcp)
                    self.timestamp_controller.set_timestamp(timestamp_reply)
                    timestamp_confirm = self.timestamp_controller.next_timestamp(min_delay)
                    confirm.time = timestamp_confirm
                    self.add_packet(confirm, ip_source, ip)

                    # 3) Build SMB Negotiation packets
                    smb_mid = rnd.randint(1, 65535)
                    smb_pid = rnd.randint(1, 65535)
                    smb_req_tail_arr = []
                    smb_req_tail_size = 0

                    # select dialects based on smb version
                    if smb_version == "1":
                        smb_req_dialects = SMBLib.smb_dialects[0:6]
                    else:
                        smb_req_dialects = SMBLib.smb_dialects
                    if len(smb_req_dialects) == 0:
                        smb_req_tail_arr.append(SMBNegociate_Protocol_Request_Tail())
                        smb_req_tail_size = len(SMBNegociate_Protocol_Request_Tail())
                    else:
                        for dia in smb_req_dialects:
                            smb_req_tail_arr.append(SMBNegociate_Protocol_Request_Tail(BufferData=dia))
                            smb_req_tail_size += len(SMBNegociate_Protocol_Request_Tail(BufferData=dia))

                    # Creation of SMB Negotiate Protocol Request packet
                    smb_req_head = SMBNegociate_Protocol_Request_Header(Flags2=0x2801, PID=smb_pid, MID=smb_mid,
                                                                        ByteCount=smb_req_tail_size)
                    smb_req_length = len(smb_req_head) + smb_req_tail_size
                    smb_req_net_bio = NBTSession(TYPE=0x00, LENGTH=smb_req_length)
                    smb_req_tcp = inet.TCP(sport=sport, dport=SMBLib.smb_port, flags='PA', seq=attacker_seq,
                                           ack=victim_seq)
                    smb_req_ip = inet.IP(src=ip_source, dst=ip, ttl=source_ttl_value)
                    smb_req_ether = inet.Ether(src=mac_source, dst=mac_destination)
                    attacker_seq += len(smb_req_net_bio) + len(smb_req_head) + smb_req_tail_size

                    smb_req_combined = (smb_req_ether / smb_req_ip / smb_req_tcp / smb_req_net_bio / smb_req_head)

                    for i in range(0, len(smb_req_tail_arr)):
                        smb_req_combined = smb_req_combined / smb_req_tail_arr[i]

                    self.timestamp_controller.set_timestamp(timestamp_confirm)
                    timestamp_smb_req = self.timestamp_controller.next_timestamp(min_delay)
                    smb_req_combined.time = timestamp_smb_req
                    self.add_packet(smb_req_combined, ip_source, ip)

                    # destination confirms SMB request package
                    reply_tcp = inet.TCP(sport=SMBLib.smb_port, dport=sport, seq=victim_seq, ack=attacker_seq,
                                         window=destination_win_value, flags='A')
                    confirm_smb_req = (reply_ether / reply_ip / reply_tcp)
                    self.timestamp_controller.set_timestamp(timestamp_smb_req)
                    timestamp_reply = self.timestamp_controller.next_timestamp(min_delay)
                    confirm_smb_req.time = timestamp_reply
                    self.add_packet(confirm_smb_req, ip_source, ip)

                    # smb response package
                    first_timestamp = time.mktime(time.strptime(first_timestamp_smb, "%Y-%m-%d %H:%M:%S"))
                    server_guid, security_blob, capabilities, data_size, server_start_time =\
                        SMBLib.get_smb_platform_data(self.host_os, first_timestamp)

                    self.timestamp_controller.set_timestamp(timestamp_reply)
                    timestamp_smb_rsp = self.timestamp_controller.next_timestamp(min_delay)
                    diff = timestamp_smb_rsp - timestamp_smb_req
                    begin = Util.get_filetime_format(timestamp_smb_req + diff * 0.1)
                    end = Util.get_filetime_format(timestamp_smb_rsp - diff * 0.1)
                    system_time = rnd.randint(begin, end)

                    # Creation of SMB Negotiate Protocol Response packets
                    if smb_version != "1" and hosting_version != "1":
                        smb_rsp_packet = SMB2.SMB2_SYNC_Header(Flags=1)
                        smb_rsp_negotiate_body =\
                            SMB2.SMB2_Negotiate_Protocol_Response(DialectRevision=0x02ff, SecurityBufferOffset=124,
                                                                  SecurityBufferLength=len(security_blob),
                                                                  SecurityBlob=security_blob, Capabilities=capabilities,
                                                                  MaxTransactSize=data_size, MaxReadSize=data_size,
                                                                  MaxWriteSize=data_size, SystemTime=system_time,
                                                                  ServerStartTime=server_start_time,
                                                                  ServerGuid=server_guid)
                        smb_rsp_length = len(smb_rsp_packet) + len(smb_rsp_negotiate_body)
                    else:
                        smb_rsp_packet =\
                            SMBNegociate_Protocol_Response_Advanced_Security(Start="\xffSMB", PID=smb_pid, MID=smb_mid,
                                                                             DialectIndex=5, SecurityBlob=security_blob)
                        smb_rsp_length = len(smb_rsp_packet)
                    smb_rsp_net_bio = NBTSession(TYPE=0x00, LENGTH=smb_rsp_length)
                    smb_rsp_tcp = inet.TCP(sport=SMBLib.smb_port, dport=sport, flags='PA', seq=victim_seq,
                                           ack=attacker_seq)
                    smb_rsp_ip = inet.IP(src=ip, dst=ip_source, ttl=destination_ttl_value)
                    smb_rsp_ether = inet.Ether(src=mac_destination, dst=mac_source)
                    victim_seq += len(smb_rsp_net_bio) + len(smb_rsp_packet)
                    if smb_version != "1" and hosting_version != "1":
                        victim_seq += len(smb_rsp_negotiate_body)

                    smb_rsp_combined = (smb_rsp_ether / smb_rsp_ip / smb_rsp_tcp / smb_rsp_net_bio / smb_rsp_packet)
                    if smb_version != "1" and hosting_version != "1":
                        smb_rsp_combined = (smb_rsp_combined / smb_rsp_negotiate_body)

                    smb_rsp_combined.time = timestamp_smb_rsp
                    self.add_packet(smb_rsp_combined, ip_source, ip)

                    # source confirms SMB response package
                    confirm_tcp = inet.TCP(sport=sport, dport=SMBLib.smb_port, seq=attacker_seq, ack=victim_seq,
                                           window=source_win_value, flags='A')
                    confirm_smb_res = (confirm_ether / confirm_ip / confirm_tcp)
                    self.timestamp_controller.set_timestamp(timestamp_smb_rsp)
                    timestamp_confirm = self.timestamp_controller.next_timestamp(min_delay)
                    confirm_smb_res.time = timestamp_confirm
                    self.add_packet(confirm_smb_res, ip_source, ip)

                    # attacker sends FIN ACK
                    confirm_tcp = inet.TCP(sport=sport, dport=SMBLib.smb_port, seq=attacker_seq, ack=victim_seq,
                                           window=source_win_value, flags='FA')
                    source_fin_ack = (confirm_ether / confirm_ip / confirm_tcp)
                    self.timestamp_controller.set_timestamp(timestamp_confirm)
                    timestamp_src_fin_ack = self.timestamp_controller.next_timestamp(min_delay)
                    source_fin_ack.time = timestamp_src_fin_ack
                    attacker_seq += 1
                    self.add_packet(source_fin_ack, ip_source, ip)

                    # victim sends FIN ACK
                    reply_tcp = inet.TCP(sport=SMBLib.smb_port, dport=sport, seq=victim_seq, ack=attacker_seq,
                                         window=destination_win_value, flags='FA')
                    destination_fin_ack = (reply_ether / reply_ip / reply_tcp)
                    self.timestamp_controller.set_timestamp(timestamp_src_fin_ack)
                    timestamp_dest_fin_ack = self.timestamp_controller.next_timestamp(min_delay)
                    victim_seq += 1
                    destination_fin_ack.time = timestamp_dest_fin_ack
                    self.add_packet(destination_fin_ack, ip_source, ip)

                    # source sends final ACK
                    confirm_tcp = inet.TCP(sport=sport, dport=SMBLib.smb_port, seq=attacker_seq, ack=victim_seq,
                                           window=source_win_value, flags='A')
                    final_ack = (confirm_ether / confirm_ip / confirm_tcp)
                    self.timestamp_controller.set_timestamp(timestamp_dest_fin_ack)
                    timestamp_final_ack = self.timestamp_controller.next_timestamp(min_delay)
                    final_ack.time = timestamp_final_ack
                    self.add_packet(final_ack, ip_source, ip)

                else:
                    # Build RST package
                    reply_ether = inet.Ether(src=mac_destination, dst=mac_source)
                    reply_ip = inet.IP(src=ip, dst=ip_source, ttl=destination_ttl_value, flags='DF')
                    reply_tcp = inet.TCP(sport=SMBLib.smb_port, dport=sport, seq=0, ack=attacker_seq, flags='RA',
                                         window=destination_win_value, options=[('MSS', destination_mss_value)])
                    reply = (reply_ether / reply_ip / reply_tcp)
                    reply.time = timestamp_reply
                    self.add_packet(reply, ip_source, ip)

            self.timestamp_controller.set_timestamp(timestamp_next_pkt)
            timestamp_next_pkt = self.timestamp_controller.next_timestamp()

    def generate_attack_pcap(self):
        """
        Creates a pcap containing the attack packets.

        :return: The location of the generated pcap file.
        """
        # store end time of attack
        self.attack_end_utime = self.packets[-1].time

        # write attack self.packets to pcap
        pcap_path = self.write_attack_pcap(sorted(self.packets, key=lambda pkt: pkt.time))

        # return packets sorted by packet time_sec_start
        return len(self.packets), pcap_path
