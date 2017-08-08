import requests
from .common import Provider


class Cryptoid(Provider):

    '''API wrapper for http://chainz.cryptoid.info blockexplorer.'''

    @classmethod
    def __init__(self, network: str):
        """
        : network = peercoin [ppc], peercoin-testnet [tppc] ...
        """

        self.net = self._netname(network)['short']
        if self.net.startswith('t') or 'testnet' in self.net:
            self.net = self.net[1:] + '-test'
        self.api_session = requests.Session()

    key = '7547f94398e3'
    api_calls = ('getblockcount', 'getdifficulty', 'getbalance',
                 'getreceivedbyaddress', 'listunspent')
    private = ('getbalance', 'unspent')
    explorer_url = 'https://chainz.cryptoid.info/explorer/'

    @classmethod
    def api_req(self, query: str) -> dict:

        api_url = 'https://chainz.cryptoid.info/{0}/api.dws'.format(self.net)

        if (p in self.api_calls for p in query):
            query = api_url + "?q=" + query

            if (p in self.private for p in query):
                query += "&key=" + self.key

            response = self.api_session.get(query)

        assert response.status_code == 200, {'error': 'API error: ' + str(response.status_code)}
        return response.json()

    @classmethod
    def block_req(self, query: str) -> dict:

        response = self.api_session.get(query)
        assert response.status_code == 200, {'error': 'API error: ' + str(response.status_code)}
        return response.json()

    @classmethod
    def getblockcount(cls) -> int:

        return cls.api_req('getblockcount')

    @classmethod
    def getblock(cls, blocknum: int) -> dict:
        '''unlike with all other providers, 
        it is only possible to query block by blocknum'''

        query = cls.explorer_url + 'block.raw.dws?coin={net}&id={blocknum}'.format(net=cls.net,
                                                                                   blocknum=blocknum)
        return cls.block_req(query)

    @classmethod
    def getblockhash(cls, blocknum: int) -> str:
        '''get blockhash'''

        query = cls.explorer_url + 'block.raw.dws?coin={net}&id={blocknum}'.format(net=cls.net,
                                                                                   blocknum=blocknum)
        return cls.block_req(query)['hash']

    @classmethod
    def getdifficulty(cls) -> float:

        return cls.api_req('getdifficulty')

    @classmethod
    def getbalance(cls, address: str) -> float:

        return float(cls.api_req('getbalance' + "&a=" + address))

    @classmethod
    def getreceivedbyaddress(cls, address: str) -> float:

        return float(cls.api_req('getreceivedbyaddress' + "&a=" + address))

    @classmethod
    def listunspent(cls, address: str) -> list:

        return cls.api_req('unspent' + "&active=" + address)['unspent_outputs']

    @classmethod
    def getrawtransaction(cls, txid: str) -> dict:

        query = cls.explorer_url + 'tx.raw.dws?coin={net}&id={txid}'.format(net=cls.net,
                                                                            txid=txid)
        return cls.block_req(query)

    @classmethod
    def listtransactions(cls, address: str) -> list:

        query = cls.explorer_url + 'address.summary.dws?coin={net}&id={addr}'.format(net=cls.net,
                                                                                     addr=address)
        resp = cls.block_req(query)
        return [i[1].lower() for i in resp['tx']]