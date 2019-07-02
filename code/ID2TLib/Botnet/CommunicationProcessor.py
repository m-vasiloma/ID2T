import os

import ID2TLib.Botnet.libbotnetcomm as lb
import ID2TLib.Utility as Util

from lea import Lea
from random import randrange
from ID2TLib.Botnet.Message import Message
from ID2TLib.Botnet.Message import MessageType

class CommunicationProcessor:
    """
    Class to process parsed input CSV/XML data and retrieve a mapping or other information.
    """

    def __init__(self, mtypes: dict, nat: bool, strategy: str, number_ids: int, max_int_time: int, start_idx: int,
                 end_idx: int):
        """
        Creates an instance of CommunicationProcessor.
        :param mtypes: a dict containing an int to EnumType mapping of MessageTypes
        :param nat: whether NAT is present in this network
        :param strategy: The selection strategy (i.e. random, optimal, custom)
        :param number_ids: The number of initiator IDs that have to exist in the interval(s)
        :param max_int_time: The maximum time period of the interval
        :param start_idx: The message index the interval should start at (None if not specified)
        :param end_idx: The message index the interval should stop at (inclusive) (None if not specified)
        """
        self.packets = []
        self.mtypes = mtypes
        self.nat = nat
        self.strategy = strategy
        self.number_ids = number_ids
        self.max_int_time = max_int_time
        self.start_idx = start_idx
        self.end_idx = end_idx
        self.interval = None
        self.messages = []
        self.respnd_ids = set()
        self.external_init_ids = set()
        self.local_init_ids = dict()
        self.local_ids = dict()
        self.external_ids = set()
        self.print_updates = False
        # use C++ communication processor for faster interval finding
        self.cpp_comm_proc = lb.botnet_comm_processor()

    def init_cpp_comm_processsor(self, filepath_xml, filepath_csv, packet_count):

        # only use CSV input if the XML path is the default one
        # --> prefer XML input over CSV input (in case both are given)
        if filepath_csv and not filepath_xml:
            filename = os.path.splitext(os.path.basename(filepath_csv))[0]
            filesize = os.path.getsize(filepath_csv) / 2**20  # get filesize in MB
            if filesize > 10:
                print("\nParsing input CSV file...", end=" ", flush=True)
                self.print_updates = True
            self.cpp_comm_proc.parse_csv(filepath_csv)
            if self.print_updates:
                print("done.")
                print("Writing corresponding XML file...", end=" ", flush=True)
            filepath_xml = self.cpp_comm_proc.write_xml(Util.OUT_DIR, filename)
            if self.print_updates:
                print("done.")
        else:
            filesize = os.path.getsize(filepath_xml) / 2**20  # get filesize in MB
            if filesize > 10:
                print("Parsing input XML file...", end=" ", flush=True)
                self.print_updates = True
            self.cpp_comm_proc.parse_xml(filepath_xml)
            if self.print_updates:
                print("done.")

        potential_long_find_time = (
                    self.strategy == "optimal" and (filesize > 4 and packet_count > 1000))
        if self.print_updates or potential_long_find_time:
            if not self.print_updates:
                print()
            print("Selecting communication interval from input CSV/XML file...", end=" ", flush=True)
            if potential_long_find_time:
                print("\nWarning: Because of the large input files and the (chosen) interval selection strategy")
                print("'optimal', this may take a while. Consider using selection strategy 'random' or 'custom'...",
                      end=" ", flush=True)
            self.print_updates = True

    def set_mapping(self, packets: list, mapped_ids: dict):
        """
        Set the selected mapping for this communication processor.

        :param packets: all packets contained in the mapped time frame
        :param mapped_ids: the chosen IDs
        """
        self.packets = packets
        self.local_init_ids = set(mapped_ids)

    def get_comm_interval(self):
        """
        Finds a communication interval with respect to the given strategy. The interval is maximum of the given seconds 
        and has at least number_ids communicating initiators in it.

        :return: A dict representing the communication interval. It contains the initiator IDs, 
                 the start index and end index of the respective interval. The respective keys 
                 are {IDs, Start, End}. If no interval is found, an empty dict is returned.
        """

        if self.strategy == "random":
            # try finding not-empty interval 5 times
            for i in range(5):
                start_idx = randrange(0, self.cpp_comm_proc.get_message_count())
                self.interval = self.cpp_comm_proc.find_interval_from_startidx(start_idx, self.number_ids, self.max_int_time)
                if self.interval and self.interval["IDs"]:
                    break
        elif self.strategy == "optimal":
            intervals = self.cpp_comm_proc.find_optimal_interval(self.number_ids, self.max_int_time)
            if intervals:
                for i in range(5):
                    self.interval = intervals[randrange(0, len(intervals))]
                    if self.interval and self.interval["IDs"]:
                        break
        elif self.strategy == "custom":
            if (not self.start_idx) and (not self.end_idx):
                print("Custom strategy was selected, but no (valid) start or end index was specified.")
                print("Because of this, a random interval is selected.")
                start_idx = randrange(0, self.cpp_comm_proc.get_message_count())
                self.interval = self.cpp_comm_proc.find_interval_from_startidx(start_idx, self.number_ids,
                                                                               self.max_int_time)
            elif (not self.start_idx) and self.end_idx:
                self.end_idx -= 1  # because message indices start with 1 (for the user)
                self.interval = self.cpp_comm_proc.find_interval_from_endidx(self.end_idx, self.number_ids,
                                                                             self.max_int_time)
            elif self.start_idx and (not self.end_idx):
                self.start_idx -= 1  # because message indices start with 1 (for the user)
                self.interval = self.cpp_comm_proc.find_interval_from_startidx(self.start_idx, self.number_ids,
                                                                               self.max_int_time)
            elif self.start_idx and self.end_idx:
                self.start_idx -= 1
                self.end_idx -= 1
                ids = self.cpp_comm_proc.get_interval_init_ids(self.start_idx, self.end_idx)
                if ids:
                    self.interval = {"IDs": ids, "Start": self.start_idx, "End": self.end_idx}

        if not self.interval or not self.interval["IDs"]:
            self.interval = {}

        if not self.interval:
            print("Error: An interval that satisfies the input cannot be found.")
        if self.print_updates:
            print("done.")  # print corresponding message to interval finding message
            print("Generating attack packets...", end=" ", flush=True)

        return self.interval

    def det_id_roles_and_msgs(self):
        """
        Determine the role of every mapped ID. The role can be initiator, responder or both.
        On the side also connect corresponding messages together to quickly find out
        which reply belongs to which request and vice versa.

        :return: the selected messages
        """

        mtypes = self.mtypes
        # setup initial variables and their values
        respnd_ids = set()
        # msgs --> the filtered messages, msg_id --> an increasing ID to give every message an artificial primary key
        msgs, msg_id = [], 0
        # keep track of previous request to find connections
        prev_reqs = {}
        # used to determine whether a request has been seen yet, so that replies before the first request are skipped
        # and do not throw an error by accessing the empty dict prev_reqs (this is not a perfect solution, but it works
        # most of the time)
        req_seen = False
        local_init_ids = self.local_init_ids
        external_init_ids = set()

        # process every packet individually 
        for packet in self.packets:
            id_src, id_dst, msg_type, time = packet["Src"], packet["Dst"], int(packet["Type"]), float(packet["Time"])
            lineno = packet.get("LineNumber", -1)
            # if if either one of the IDs is not mapped, continue
            if (id_src not in local_init_ids) and (id_dst not in local_init_ids):
                continue

            # convert message type number to enum type
            msg_type = mtypes[msg_type]

            # process a request
            if msg_type in {MessageType.SALITY_HELLO, MessageType.SALITY_NL_REQUEST}:
                if not self.nat and id_dst in local_init_ids and id_src not in local_init_ids:
                    external_init_ids.add(id_src)
                elif id_src not in local_init_ids:
                    continue
                else:
                    # process ID's role
                    respnd_ids.add(id_dst)
                # convert the abstract message into a message object to handle it better
                msg_str = "{0}-{1}".format(id_src, id_dst)
                msg = Message(msg_id, id_src, id_dst, msg_type, time, line_no=lineno)
                msgs.append(msg)
                prev_reqs[msg_str] = msg_id
                msg_id += 1
                req_seen = True

            # process a reply
            elif msg_type in {MessageType.SALITY_HELLO_REPLY, MessageType.SALITY_NL_REPLY} and req_seen:
                if not self.nat and id_src in local_init_ids and id_dst not in local_init_ids:
                    # process ID's role
                    external_init_ids.add(id_dst)
                elif id_dst not in local_init_ids:
                    continue
                else: 
                    # process ID's role
                    respnd_ids.add(id_src)
                # convert the abstract message into a message object to handle it better
                msg_str = "{0}-{1}".format(id_dst, id_src)
                # find the request message ID for this response and set its reference index
                refer_idx = prev_reqs.get(msg_str, -1)
                if refer_idx != -1:
                    msgs[refer_idx].refer_msg_id = msg_id
                    del(prev_reqs[msg_str])
                msg = Message(msg_id, id_src, id_dst, msg_type, time, refer_idx, lineno)
                msgs.append(msg)
                # remove the request to this response from storage
                msg_id += 1

            elif msg_type == MessageType.TIMEOUT and id_src in local_init_ids and not self.nat:
                # convert the abstract message into a message object to handle it better
                msg_str = "{0}-{1}".format(id_dst, id_src)
                # find the request message ID for this response and set its reference index
                refer_idx = prev_reqs.get(msg_str)
                if refer_idx is not None:
                    msgs[refer_idx].refer_msg_id = msg_id
                    if msgs[refer_idx].type == MessageType.SALITY_NL_REQUEST:
                        msg = Message(msg_id, id_src, id_dst, MessageType.SALITY_NL_REPLY, time, refer_idx, lineno)
                    else:
                        msg = Message(msg_id, id_src, id_dst, MessageType.SALITY_HELLO_REPLY, time, refer_idx, lineno)
                    msgs.append(msg)
                    # remove the request to this response from storage
                    del(prev_reqs[msg_str])
                    msg_id += 1

        # store the retrieved information in this object for later use
        self.respnd_ids = sorted(respnd_ids)
        self.external_init_ids = sorted(external_init_ids)
        self.messages = msgs

        # return the selected messages
        return self.messages

    def get_messages(self):
        """
        Get ID mapping of the abstract packets and retrieve messages for them.
        Using cpp_comm_proc.get_messages() and det_id_roles_and_msgs().

        :return: messages selected by det_id_roles_and_msgs()
        """

        # retrieve the mapping information
        mapped_ids = self.interval["IDs"]
        packet_start_idx = self.interval["Start"]
        packet_end_idx = self.interval["End"]
        while len(mapped_ids) > self.number_ids:
            rm_idx = randrange(0, len(mapped_ids))
            del mapped_ids[rm_idx]

        # get the messages contained in the chosen interval
        abstract_packets = self.cpp_comm_proc.get_messages(packet_start_idx, packet_end_idx)
        self.set_mapping(abstract_packets, mapped_ids)
        # determine ID roles and select the messages that are to be mapped into the PCAP
        return self.det_id_roles_and_msgs()

    def det_ext_and_local_ids(self, prob_rspnd_local: int=0):
        """
        Map the given IDs to a locality (i.e. local or external} considering the given probabilities.

        :param prob_rspnd_local: the probabilty that a responder is local
        """
        external_ids = set()
        local_ids = self.local_init_ids.copy()
        
        # set up probabilistic chooser
        rspnd_locality = Lea.fromValFreqsDict({"local": prob_rspnd_local*100, "external": (1-prob_rspnd_local)*100})

        for id_ in self.external_init_ids:
            external_ids.add(id_)

        # determine responder localities
        for id_ in self.respnd_ids:
            if id_ in local_ids or id_ in external_ids:
                continue 
            
            pos = rspnd_locality.random() 
            if pos == "local":
                local_ids.add(id_)
            elif pos == "external":
                external_ids.add(id_)

        self.local_ids, self.external_ids = local_ids, external_ids
        return self.local_ids, self.external_ids
