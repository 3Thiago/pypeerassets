
'''Communicate with local or remote peercoin-daemon via JSON-RPC'''

from operator import itemgetter

try:
    from peercoin_rpc import Client
except:
    raise EnvironmentError("peercoin_rpc library is required for this to work,\
                            use pip to install it.")

def select_inputs(cls, total_amount):
    '''finds apropriate utxo's to include in rawtx, while being careful
    to never spend old transactions with a lot of coin age.
    Argument is intiger, returns list of apropriate UTXO's'''

    my_addresses = [i["address"] for i in cls.listreceivedbyaddress()]

    utxo = []
    utxo_sum = float(-0.01) ## starts from negative due to minimal fee
    for tx in sorted(cls.listunspent(), key=itemgetter('confirmations')):

        for v in cls.getrawtransaction(tx["txid"])["vout"]:
            if v["scriptPubKey"]["addresses"][0] in my_addresses:
                utxo.append({
                    "txid": tx["txid"],
                    "vout": v["n"],
                    "ScriptSig": v["scriptPubKey"]["hex"]
                })

                utxo_sum += float(v["value"])
                if utxo_sum >= total_amount:
                    return utxo

    if utxo_sum < total_amount:
        raise ValueError("Not enough funds.")

Client.select_inputs = select_inputs
