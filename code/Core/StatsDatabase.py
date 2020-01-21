import os.path
import random as rnd
import typing
import sqlite3
import sys

import Lib.libpcapreader as pr
import Core.QueryParser as qp
import pyparsing as pp


def dict_gen(curs: sqlite3.Cursor):
    """
    Generates a dictionary of a sqlite3.Cursor object by fetching the query's results.
    Taken from Python Essential Reference by David Beazley.
    """
    field_names = [d[0] for d in curs.description]
    while True:
        rows = curs.fetchmany()
        if not rows:
            return
        for row in rows:
            yield dict(zip(field_names, row))


class QueryExecutionException(Exception):
    pass


class StatsDatabase:
    def __init__(self, db_path: str):
        """
        Creates a new StatsDatabase.

        :param db_path: The path to the database file
        """
        self.query_parser = qp.QueryParser()

        self.existing_db = os.path.exists(db_path)
        self.database = sqlite3.connect(db_path)
        self.cursor = self.database.cursor()
        self.current_interval_statistics_tables = []

        # If DB not existing, create a new DB scheme
        if self.existing_db:
            if self.get_db_outdated():
                print('Statistics database outdated. Recreating database at: ', db_path)
            else:
                print('Located statistics database at: ', db_path)
        else:
            print('Statistics database not found. Creating new database at: ', db_path)

    def get_file_info(self):
        """
        Retrieves general file statistics from the database. This includes:

        - packetCount           : Number of packets in the PCAP file
        - captureDuration       : Duration of the packet capture in seconds
        - timestampFirstPacket  : Timestamp of the first captured packet
        - timestampLastPacket   : Timestamp of the last captured packet
        - avgPacketRate         : Average packet rate
        - avgPacketSize         : Average packet size
        - avgPacketsSentPerHost : Average number of packets sent per host
        - avgBandwidthIn        : Average incoming bandwidth
        - avgBandwidthOut       : Average outgoing bandwidth

        :return: a dictionary of keys (see above) and their respective values
        """
        return [r for r in dict_gen(
            self.cursor.execute('SELECT * FROM file_statistics'))][0]

    def get_db_exists(self):
        """
        :return: True if the database was already existent, otherwise False
        """
        return self.existing_db

    def get_db_outdated(self):
        """
        Retrieves the database version from the database and compares it to the version
        it should have to check whether the database is outdated and needs to be recreated.
        :return: True if the versions match, otherwise False
        """
        self.cursor.execute('PRAGMA user_version;')
        return self.cursor.fetchall()[0][0] != pr.pcap_processor.get_db_version()

    @staticmethod
    def _get_selector_keywords():
        """
        :return: a list of selector keywords
        """
        return ['most_used', 'least_used', 'avg', 'all']

    @staticmethod
    def _get_parametrized_selector_keywords():
        """
        :return: a list of parameterizable selector keywords
        """
        return ['ipaddress', 'macaddress']

    @staticmethod
    def _get_extractor_keywords():
        """

        :return: a list of extractor keywords
        """
        return ['random', 'first', 'last']

    def get_all_named_query_keywords(self):
        """

        :return: a list of all named query keywords, used to identify named queries
        """
        return (
            self._get_selector_keywords() + self._get_parametrized_selector_keywords() + self._get_extractor_keywords())

    @staticmethod
    def get_all_sql_query_keywords():
        """
        :return: a list of all supported SQL keywords, used to identify SQL queries
        """
        return ["select", "insert"]

    def process_user_defined_query(self, query_string: str, query_parameters: tuple = None):
        """
        Takes as input a SQL query query_string and optional a tuple of parameters which are marked by '?' in the query
        and later substituted.

        :param query_string: The query to execute
        :param query_parameters: The tuple of parameters to inject into the query
        :return: the results of the query
        """
        if query_parameters is not None:
            self.cursor.execute(query_string, query_parameters)
        else:
            self.cursor.execute(query_string)
        self.database.commit()
        return self.cursor.fetchall()

    def get_field_types(self, *table_names):
        """
        Creates a dictionary whose keys are the fields of the given table(s) and whose values are the appropriate field
        types, like TEXT for strings and REAL for float numbers.

        :param table_names: The name of table(s)
        :return: a dictionary of {field_name : field_type} for fields of all tables
        """
        dic = {}
        for table in table_names:
            self.cursor.execute("PRAGMA table_info('%s')" % table)
            results = self.cursor.fetchall()
            for field in results:
                dic[field[1].lower()] = field[2]
        return dic

    def get_current_interval_statistics_table(self):
        """
        :return: the current interval statistics table used for internal calculations
        """
        if len(self.current_interval_statistics_tables) > 0:
            return self.current_interval_statistics_tables[0]
        else:
            return ""

    def get_all_current_interval_statistics_tables(self):
        """
        :return: the list of all current interval statistics tables
        """
        if len(self.current_interval_statistics_tables) == 0:
            return [self.process_db_query("SELECT name FROM interval_tables WHERE is_default=1")]
        return self.current_interval_statistics_tables

    def set_current_interval_statistics_tables(self, current_intervals: list):
        """
        Sets the current interval statistics table, which should be used for internal calculations.
        :param current_intervals: a list of current intervals in seconds, first of which should be used for internal
                                  calculations
        """
        for current_interval in current_intervals:
            if current_interval == 0.0:
                table_name = self.process_db_query("SELECT name FROM interval_tables WHERE is_default=1")
                if table_name != []:
                    substr = "Using default interval: " + str(float(table_name[len("interval_statistics_"):])/1000000) \
                             + "s"
                else:
                    substr = "The default interval will used after it is calculated."
                print("No user specified interval found. " + substr)
            else:
                table_name = "interval_statistics_" + str(int(current_interval*1000000))
                if current_interval == current_intervals[0]:
                    print("User specified interval(s) found. Using first interval length given for internal "
                          "calculations: " + str(current_interval) + "s")
            self.current_interval_statistics_tables.append(table_name)

    def named_query_parameterized(self, keyword: str, param_op_val: list):
        """
        Executes a parameterizable named query.

        :param keyword: The query to be executed, like ipaddress or macadress
        :param param_op_val: A list consisting of triples with (parameter, operator, value)
        :return: the results of the executed query
        """
        named_queries = {
            "ipaddress": "SELECT DISTINCT ip_statistics.ipAddress from ip_statistics INNER JOIN ip_mac, ip_ttl, "
                         "ip_ports, ip_protocols ON ip_statistics.ipAddress=ip_mac.ipAddress AND "
                         "ip_statistics.ipAddress=ip_ttl.ipAddress AND ip_statistics.ipAddress=ip_ports.ipAddress "
                         "AND ip_statistics.ipAddress=ip_protocols.ipAddress WHERE ",
            "macaddress": "SELECT DISTINCT macAddress from ip_mac WHERE "}
        query = named_queries.get(keyword)
        field_types = self.get_field_types('ip_mac', 'ip_ttl', 'ip_ports', 'ip_protocols', 'ip_statistics', 'ip_mac')
        conditions = []
        for key, op, value in param_op_val:
            # Check whether the value is not a simple value, but another query (or list)
            if isinstance(value, pp.ParseResults):
                if value[0] == "list":
                    # We have a list, cut the token off and use the remaining elements
                    value = value[1:]

                    # Lists can only be used with "in"
                    if op != "in":
                        raise QueryExecutionException("List values require the usage of the 'in' operator!")
                else:
                    # If we have another query instead of a direct value, execute and replace it
                    rvalue = self._execute_query_list(value)

                    # Do we have a comparison operator with a multiple-result query?
                    if op != "in" and value[0] in ['most_used', 'least_used', 'all', 'ipaddress_param',
                                                       'macaddress_param']:
                        raise QueryExecutionException("The extractor '" + value[0] +
                                                      "' may return more than one result!")

                    # Make value contain a simple list with the results of the query
                    value = map(lambda x: str(x[0]), rvalue)
            else:
                # Make sure value is a list now to simplify handling
                value = [value]

            # this makes sure that TEXT fields are queried by strings,
            # e.g. ipAddress=192.168.178.1 --is-converted-to--> ipAddress='192.168.178.1'
            if field_types.get(key) == 'TEXT':
                def ensure_string(x):
                    if not str(x).startswith("'") and not str(x).startswith('"'):
                        return "'" + x + "'"
                    else:
                        return x
                value = map(ensure_string, value)

            # If we have more than one value, join them together, separated by commas
            value = ",".join(map(str, value))

            # this replacement is required to remove ambiguity in SQL query
            if key == 'ipAddress':
                key = 'ip_mac.ipAddress'
            conditions.append(key + " " + op + " (" + str(value) + ")")

        where_clause = " AND ".join(conditions)
        query += where_clause
        self.cursor.execute(query)
        return self.cursor.fetchall()

    named_queries = {
        "most_used.ipaddress": "SELECT ipAddress FROM ip_statistics WHERE (pktsSent+pktsReceived) == "
                               "(SELECT MAX(pktsSent+pktsReceived) from ip_statistics) ORDER BY ipAddress ASC",
        "most_used.macaddress": "SELECT macAddress FROM (SELECT macAddress, COUNT(*) as occ from ip_mac GROUP BY "
                                "macAddress) WHERE occ=(SELECT COUNT(*) as occ from ip_mac GROUP BY macAddress "
                                "ORDER BY occ DESC LIMIT 1) ORDER BY macAddress ASC",
        "most_used.portnumber": "SELECT portNumber FROM ip_ports GROUP BY portNumber HAVING COUNT(portNumber)="
                                "(SELECT MAX(cntPort) from (SELECT portNumber, COUNT(portNumber) as cntPort FROM "
                                "ip_ports GROUP BY portNumber)) ORDER BY portNumber ASC",
        "most_used.protocolname": "SELECT protocolName FROM ip_protocols GROUP BY protocolName HAVING "
                                  "COUNT(protocolCount)=(SELECT COUNT(protocolCount) as cnt FROM ip_protocols "
                                  "GROUP BY protocolName ORDER BY cnt DESC LIMIT 1) ORDER BY protocolName ASC",
        "most_used.ttlvalue": "SELECT ttlValue FROM (SELECT ttlValue, SUM(ttlCount) as occ FROM ip_ttl GROUP BY "
                              "ttlValue) WHERE occ=(SELECT SUM(ttlCount) as occ FROM ip_ttl GROUP BY ttlValue "
                              "ORDER BY occ DESC LIMIT 1) ORDER BY ttlValue ASC",
        "most_used.mssvalue": "SELECT mssValue FROM (SELECT mssValue, SUM(mssCount) as occ FROM tcp_mss GROUP BY "
                              "mssValue) WHERE occ=(SELECT SUM(mssCount) as occ FROM tcp_mss GROUP BY mssValue "
                              "ORDER BY occ DESC LIMIT 1) ORDER BY mssValue ASC",
        "most_used.winsize": "SELECT winSize FROM (SELECT winSize, SUM(winCount) as occ FROM tcp_win GROUP BY "
                             "winSize) WHERE occ=(SELECT SUM(winCount) as occ FROM tcp_win GROUP BY winSize ORDER "
                             "BY occ DESC LIMIT 1) ORDER BY winSize ASC",
        "most_used.ipclass": "SELECT ipClass FROM (SELECT ipClass, COUNT(*) as occ from ip_statistics GROUP BY "
                             "ipClass ORDER BY occ DESC) WHERE occ=(SELECT COUNT(*) as occ from ip_statistics "
                             "GROUP BY ipClass ORDER BY occ DESC LIMIT 1) ORDER BY ipClass ASC",
        "least_used.ipaddress": "SELECT ipAddress FROM ip_statistics WHERE (pktsSent+pktsReceived) == (SELECT "
                                "MIN(pktsSent+pktsReceived) from ip_statistics) ORDER BY ipAddress ASC",
        "least_used.macaddress": "SELECT macAddress FROM (SELECT macAddress, COUNT(*) as occ from ip_mac GROUP "
                                 "BY macAddress) WHERE occ=(SELECT COUNT(*) as occ from ip_mac GROUP BY macAddress "
                                 "ORDER BY occ ASC LIMIT 1) ORDER BY macAddress ASC",
        "least_used.portnumber": "SELECT portNumber FROM ip_ports GROUP BY portNumber HAVING COUNT(portNumber)="
                                 "(SELECT MIN(cntPort) from (SELECT portNumber, COUNT(portNumber) as cntPort FROM "
                                 "ip_ports GROUP BY portNumber)) ORDER BY portNumber ASC",
        "least_used.protocolname": "SELECT protocolName FROM ip_protocols GROUP BY protocolName HAVING "
                                   "COUNT(protocolCount)=(SELECT COUNT(protocolCount) as cnt FROM ip_protocols "
                                   "GROUP BY protocolName ORDER BY cnt ASC LIMIT 1) ORDER BY protocolName ASC",
        "least_used.ttlvalue": "SELECT ttlValue FROM (SELECT ttlValue, SUM(ttlCount) as occ FROM ip_ttl GROUP BY "
                               "ttlValue) WHERE occ=(SELECT SUM(ttlCount) as occ FROM ip_ttl GROUP BY ttlValue "
                               "ORDER BY occ ASC LIMIT 1) ORDER BY ttlValue ASC",
        "least_used.mssvalue": "SELECT mssValue FROM (SELECT mssValue, SUM(mssCount) as occ FROM tcp_mss GROUP BY "
                               "mssValue) WHERE occ=(SELECT SUM(mssCount) as occ FROM tcp_mss GROUP BY mssValue "
                               "ORDER BY occ ASC LIMIT 1) ORDER BY mssValue ASC",
        "least_used.winsize": "SELECT winSize FROM (SELECT winSize, SUM(winCount) as occ FROM tcp_win GROUP BY "
                              "winSize) WHERE occ=(SELECT SUM(winCount) as occ FROM tcp_win GROUP BY winSize "
                              "ORDER BY occ ASC LIMIT 1) ORDER BY winSize ASC",
        "least_used.ipclass": "SELECT ipClass FROM (SELECT ipClass, COUNT(*) as occ from ip_statistics GROUP BY "
                             "ipClass ORDER BY occ DESC) WHERE occ=(SELECT COUNT(*) as occ from ip_statistics "
                             "GROUP BY ipClass ORDER BY occ ASC LIMIT 1) ORDER BY ipClass ASC",
        "avg.pktsreceived": "SELECT avg(pktsReceived) from ip_statistics",
        "avg.pktssent": "SELECT avg(pktsSent) from ip_statistics",
        "avg.kbytesreceived": "SELECT avg(kbytesReceived) from ip_statistics",
        "avg.kbytessent": "SELECT avg(kbytesSent) from ip_statistics",
        "avg.ttlvalue": "SELECT avg(ttlValue) from ip_ttl",
        "avg.mss": "SELECT avg(mssValue) from tcp_mss",
        "all.ipaddress": "SELECT ipAddress from ip_statistics ORDER BY ipAddress ASC",
        "all.ttlvalue": "SELECT DISTINCT ttlValue from ip_ttl ORDER BY ttlValue ASC",
        "all.mss": "SELECT DISTINCT mssValue from tcp_mss ORDER BY mssValue ASC",
        "all.macaddress": "SELECT DISTINCT macAddress from ip_mac ORDER BY macAddress ASC",
        "all.portnumber": "SELECT DISTINCT portNumber from ip_ports ORDER BY portNumber ASC",
        "all.protocolname": "SELECT DISTINCT protocolName from ip_protocols ORDER BY protocolName ASC",
        "all.winsize": "SELECT DISTINCT winSize FROM tcp_win ORDER BY winSize ASC",
        "all.ipclass": "SELECT DISTINCT ipClass FROM ip_statistics ORDER BY ipClass ASC"}

    def _execute_query_list(self, query_list):
        """
        Recursively executes a list of named queries. They are of the following form:
        ['macaddress_param', [['ipaddress', 'in', ['most_used', 'ipaddress']]]]
        :param query_list: The query statement list obtained from the query parser
        :return: The result of the query (either a single result or a list).
        """
        if query_list[0] == "random":
            return [rnd.choice(self._execute_query_list(query_list[1:]))]
        elif query_list[0] == "first":
            return [self._execute_query_list(query_list[1:])[0]]
        elif query_list[0] == "last":
            return [self._execute_query_list(query_list[1:])[-1]]
        elif query_list[0] == "macaddress_param":
            return self.named_query_parameterized("macaddress", query_list[1])
        elif query_list[0] == "ipaddress_param":
            return self.named_query_parameterized("ipaddress", query_list[1])
        else:
            query = self.named_queries.get(query_list[0] + "." + query_list[1])
            if query is None:
                raise QueryExecutionException("The requested query '" + query_list[0] + "(" + query_list[1] +
                                              ")' was not found in the internal query list!")
            self.cursor.execute(str(query))
            # TODO: fetch query on demand
            last_result = self.cursor.fetchall()
            return last_result

    def process_db_query(self, query_string_in: str, print_results=False, sql_query_parameters: tuple = None):
        """
        Processes a database query. This can either be a standard SQL query or a named query (predefined query).

        :param query_string_in: The string containing the query
        :param print_results: Indicated whether the results should be printed to terminal (True) or not (False)
        :param sql_query_parameters: Parameters for the SQL query (optional)
        :return: the results of the query
        """
        named_query_keywords = self.get_all_named_query_keywords()

        # Clean query_string
        query_string = query_string_in.lower().lstrip()

        # query_string is a user-defined SQL query
        result = None
        if sql_query_parameters is not None or query_string.startswith("select") or query_string.startswith("insert"):
            result = self.process_user_defined_query(query_string, sql_query_parameters)
        # query string is a named query -> parse it and pass it to statisticsDB
        elif any(k in query_string for k in named_query_keywords) and all(k in query_string for k in ['(', ')']):
            if query_string[-1] != ";":
                query_string += ";"
            query_list = self.query_parser.parse_query(query_string)
            result = self._execute_query_list(query_list)
        else:
            sys.stderr.write(
                "Query invalid. Only named queries and SQL SELECT/INSERT allowed. Please check the query's syntax!\n")
            return

        # If result is tuple/list with single element, extract value from list
        requires_extraction = (isinstance(result, list) or isinstance(result, tuple)) and len(result) == 1 and \
                              (not isinstance(result[0], tuple) or len(result[0]) == 1)

        while requires_extraction:
            if isinstance(result, list) or isinstance(result, tuple):
                result = result[0]
            else:
                requires_extraction = False

        # If tuple of tuples or list of tuples, each consisting of single element is returned,
        # then convert it into list of values, because the returned column is clearly specified by the given query
        if (isinstance(result, tuple) or isinstance(result, list)) and all(len(val) == 1 for val in result):
            result = [c for c in result for c in c]

        # Print results if option print_results is True
        if print_results:
            if isinstance(result, list) and len(result) == 1:
                result = result[0]
                print("Query returned 1 record:\n")
                for i in range(0, len(result)):
                    print(str(self.cursor.description[i][0]) + ": " + str(result[i]))
            else:
                self._print_query_results(query_string_in, result if isinstance(result, list) else [result])

        return result

    def process_interval_statistics_query(self, query_string_in: str, table_param: str=""):
        """

        :param query_string_in: a query to be executed over the current internal interval statistics table
        :param table_param: a name of a specific interval statistics table
        :return: the result of the query
        """
        if table_param != "":
            table_name = table_param
        elif self.get_current_interval_statistics_table() != "":
            table_name = self.get_current_interval_statistics_table()
        else:
            table_name = self.process_db_query("SELECT name FROM interval_tables WHERE is_default=1")
        return self.process_user_defined_query(query_string_in % table_name)

    def _print_query_results(self, query_string_in: str, result: typing.List[typing.Union[str, float, int]]) -> None:
        """
        Prints the results of a query.
        Based on http://stackoverflow.com/a/20383011/3017719.

        :param query_string_in: The query the results belong to
        :param result: The list of query results
        """
        # Print number of results according to type of result
        if len(result) == 1:
            print("Query returned 1 record:\n")
        else:
            print("Query returned " + str(len(result)) + " records:\n")

        # Print query results
        if query_string_in.lstrip().upper().startswith(
                "SELECT") and result is not None and self.cursor.description is not None:
            widths = []
            columns = []
            tavnit = '|'
            separator = '+'
            for index, cd in enumerate(self.cursor.description):
                max_col_length = 0
                if len(result) > 0:
                    max_col_length = max(list(map(lambda x:
                                                  len(str(x[index] if len(self.cursor.description) > 1 else x)),
                                                  result)))
                widths.append(max(len(cd[0]), max_col_length))
                columns.append(cd[0])
            for w in widths:
                tavnit += " %-" + "%ss |" % (w,)
                separator += '-' * w + '--+'
            print(separator)
            print(tavnit % tuple(columns))
            print(separator)
            for row in result:
                print(tavnit % row)
            print(separator)
        else:
            print(result)
