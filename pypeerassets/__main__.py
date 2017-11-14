
'''contains main protocol logic like assembly of proof-of-timeline and parsing deck info'''

import concurrent.futures
from binascii import unhexlify
from typing import Union, Generator
from .protocol import *
from .provider import *
from .pautils import (load_deck_p2th_into_local_node, 
                      find_tx_sender,
                      find_deck_spawns, tx_serialization_order,
                      read_tx_opreturn, deck_issue_mode,
                      issue_mode_to_enum, parse_deckspawn_metainfo,
                      validate_deckspawn_p2th, validate_card_transfer_p2th,
                      parse_card_transfer_metainfo, postprocess_card
                      )
from .voting import *
from .exceptions import *
from .transactions import (nulldata_script, tx_output, p2pkh_script,
                           find_parent_outputs, calculate_tx_fee,
                           make_raw_transaction, TxOut)
from .constants import param_query, params
from .networks import query, networks


def find_all_valid_decks(provider: Provider, deck_version: int, prod: bool=True) -> Generator:
    '''
    Scan the blockchain for PeerAssets decks, returns list of deck objects.
    : provider - provider instance
    : version - deck protocol version (0, 1, 2, ...)
    : test True/False - test or production P2TH
    '''

    if isinstance(provider, RpcNode):
        deck_spawns = (provider.getrawtransaction(i, 1) for i in find_deck_spawns(provider))
    else:
        pa_params = param_query(provider.network)
        if prod:
            deck_spawns = (provider.getrawtransaction(i, 1) for i in
                           provider.listtransactions(pa_params.P2TH_addr))
        if not prod:
            deck_spawns = (provider.getrawtransaction(i, 1) for i in
                           provider.listtransactions(pa_params.test_P2TH_addr))

    def deck_parser(args: Union[dict, int]) -> Deck:
        '''main deck parser function'''

        raw_tx = args[0]
        deck_version = args[1]

        try:
            validate_deckspawn_p2th(provider, raw_tx, prod=prod)

            d = parse_deckspawn_metainfo(read_tx_opreturn(raw_tx), deck_version)

            if d:

                d["id"] = raw_tx["txid"]
                try:
                    d["time"] = raw_tx["blocktime"]
                except KeyError:
                    d["time"] = 0
                d["issuer"] = find_tx_sender(provider, raw_tx)
                d["network"] = provider.network
                d["production"] = prod
                return Deck(**d)

        except (InvalidDeckSpawn, InvalidDeckMetainfo, InvalidDeckVersion, InvalidNulldataOutput) as err:
            pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as th:
        for result in th.map(deck_parser, ((deck, deck_version) for deck in deck_spawns)):
            if result:
                yield result


def find_deck(provider: Provider, key: str, version: int, prod=True) -> list:
    '''
    Find specific deck by key, with key being:
    <id>, <name>, <issuer>, <issue_mode>, <number_of_decimals>
    '''

    decks = find_all_valid_decks(provider, version, prod=prod)
    return [d for d in decks if key in d.__dict__.values()]


def deck_spawn(provider: Provider, key: Kutil, deck: Deck, inputs: dict, change_address: str) -> str:
    '''Creates Deck spawn raw transaction.
       : key - Kutil object which we'll use to sign the tx
       : deck - Deck object
       : card - CardTransfer object
       : inputs - utxos (has to be owned by deck issuer)
       : change_address - address to send the change to
    '''

    network_params = query(deck.network)
    pa_params = param_query(deck.network)

    if deck.production:
        p2th_addr = pa_params.P2TH_addr
    else:
        p2th_addr = pa_params.test_P2TH_addr

    #  first round of txn making is done by presuming minimal fee
    change_sum = int(inputs['value']) - network_params.min_tx_fee - pa_params.P2TH_fee

    txouts = [
        tx_output(value=pa_params.P2TH_fee, seq=0, script=p2pkh_script(p2th_addr)),  # p2th
        tx_output(value=0, seq=1, script=nulldata_script(deck.metainfo_to_protobuf)),  # op_return
        tx_output(value=change_sum, seq=2, script=p2pkh_script(change_address))  # change
              ]

    mutable_tx = make_raw_transaction(inputs, txouts)

    parent_output = find_parent_outputs(provider, mutable_tx.ins[0])
    signed = key.sign_transaction(parent_output, mutable_tx)

    # if 0.01 ppc fee is enough to cover the tx size
    if network_params.min_tx_fee == calculate_tx_fee(signed.size):
        return signed.hexlify()

    fee = calculate_tx_fee(signed.size)
    change_sum = float(inputs['value']) - float(fee) - float(pa_params.P2TH_fee)

    # change output is last of transaction outputs
    txouts[-1] = tx_output(value=change_sum, seq=txouts[-1].seq, script=txouts[-1].script)

    mutable_tx = make_raw_transaction(inputs['utxos'], txouts)
    signed = key.sign_transaction(parent_output, mutable_tx)

    return signed.hexlify()


def deck_transfer(provider: Provider, key: Kutil, deck: Deck,
                  inputs: list, change_address: str):
    '''
    The deck transfer transaction is a special case of the deck spawn transaction.
    Instead of registering a new asset, the deck transfer transaction transfers ownership from vin[1] to vin[0],
    meaning that both parties are required to sign the transfer transaction for it to be accepted in the blockchain.
    '''
    raise NotImplementedError


def find_card_transfers(provider: Provider, deck: Deck) -> Generator:
    '''find all <deck> card transfers'''

    if isinstance(provider, RpcNode):
        card_transfers = (provider.getrawtransaction(i["txid"], 1) for i in
                          provider.listtransactions(deck.id))
    else:
        card_transfers = (provider.getrawtransaction(i, 1) for i in
                          provider.listtransactions(deck.p2th_address))

    def card_parser(args) -> list:
        '''this function wraps all the card transfer parsing'''

        provider = args[0]
        deck = args[1]
        raw_tx = args[2]

        try:
            validate_card_transfer_p2th(deck, raw_tx)  # validate P2TH first
            card_metainfo = parse_card_transfer_metainfo(read_tx_opreturn(raw_tx), deck.version)
            vouts = raw_tx["vout"]
            sender = find_tx_sender(provider, raw_tx)

            try:  # try to get block seq number
                blockseq = tx_serialization_order(provider, raw_tx["blockhash"], raw_tx["txid"])
            except KeyError:
                blockseq = None
            try:  # try to get block number of block when this tx was written
                blocknum = provider.getblock(raw_tx["blockhash"])["height"]
            except KeyError:
                blocknum = None

            cards = postprocess_card(card_metainfo, raw_tx, sender,
                                     vouts, blockseq, blocknum, deck)

        except (InvalidCardTransferP2TH, CardVersionMistmatch, CardNumberOfDecimalsMismatch) as e:
            return False

        return cards

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as th:
        for result in th.map(card_parser, ((provider, deck, i) for i in card_transfers)):
            if result:
                return (CardTransfer(**i) for i in result)


def card_issue(provider: Provider, key: Kutil, deck: Deck,
               card: CardTransfer, inputs: dict,
               change_address: str) -> str:
    '''Create card issue transaction.
       : key - Kutil object which we'll use to sign the tx
       : deck - Deck object
       : card - CardTransfer object
       : inputs - utxos (has to be owned by deck issuer)
       : change_address - address to send the change to
    '''

    network_params = query(deck.network)
    pa_params = param_query(deck.network)

    txouts = [
        tx_output(value=pa_params.P2TH_fee, seq=0, script=p2pkh_script(deck.p2th_address)),  # deck p2th
        tx_output(value=0, seq=1, script=nulldata_script(card.metainfo_to_protobuf))  # op_return
    ]

    for addr, index in zip(card.receiver, range(len(card.receiver))):
        txouts.append(   # TxOut for each receiver, index + 2 because we have two outs already
            tx_output(value=0, seq=index+2, script=p2pkh_script(addr))
        )

    #  first round of txn making is done by presuming minimal fee
    change_sum = float(inputs['total']) - float(network_params.min_tx_fee) - float(pa_params.P2TH_fee)

    txouts.append(
        tx_output(value=change_sum, seq=len(txouts)+1, script=p2pkh_script(change_address))
        )

    mutable_tx = make_raw_transaction(inputs['utxos'], txouts)

    parent_output = find_parent_outputs(provider, mutable_tx.ins[0])
    signed = key.sign_transaction(parent_output, mutable_tx)

    # if 0.01 ppc fee is enough to cover the tx size
    if network_params.min_tx_fee == calculate_tx_fee(signed.size):
        return signed.hexlify()

    fee = calculate_tx_fee(signed.size)
    change_sum = float(inputs['total']) - float(fee) - float(pa_params.P2TH_fee)

    # change output is last of transaction outputs
    txouts[-1] = tx_output(value=change_sum, seq=txouts[-1].seq, script=txouts[-1].script)

    mutable_tx = make_raw_transaction(inputs['utxos'], txouts)
    signed = key.sign_transaction(parent_output, mutable_tx)

    return signed.hexlify()



def card_burn(deck: Deck, card: CardTransfer, inputs: list, change_address: str) -> str:
    '''Create card burn transaction, cards are burned by sending the cards back to deck issuer.
       : deck - Deck object
       : card - CardTransfer object
       : inputs - utxos (has to be owned by deck issuer)
       : change_address - address to send the change to
       '''

    assert deck.issuer == card.receiver[0], {"error": "First recipient must be deck issuer."}

    network_params = query(deck.network)
    pa_params = param_query(deck.network)

    outputs = [
        tx_output(value=pa_params.P2TH_fee, seq=0, script=p2pkh_script(deck.p2th_address)),  # deck p2th
        tx_output(value=0, seq=1, script=nulldata_script(card.metainfo_to_protobuf)),  # op_return
        tx_output(value=0, seq=2, script=p2pkh_script(card.receiver[0]))  # p2pkh receiver[0]
    ]

    #  first round of txn making is done by presuming minimal fee
    change_sum = float(inputs['total']) - float(network_params.min_tx_fee) - float(pa_params.P2TH_fee)

    outputs.append(
        tx_output(value=change_sum, seq=len(outputs)+1, script=p2pkh_script(change_address))
        )

    return make_raw_transaction(inputs['utxos'], outputs)


def card_transfer(deck: Deck, card: CardTransfer, inputs: list, change_address: str) -> str:
    '''Standard peer-to-peer card transfer.
       : deck - Deck object
       : card - CardTransfer object
       : inputs - utxos (has to be owned by deck issuer)
       : change_address - address to send the change to
       '''

    network_params = query(deck.network)
    pa_params = param_query(deck.network)

    outputs = [
        tx_output(value=pa_params.P2TH_fee, seq=0, script=p2pkh_script(deck.p2th_address)),  # deck p2th
        tx_output(value=0, seq=1, script=nulldata_script(card.metainfo_to_protobuf))  # op_return
    ]

    for addr, index in zip(card.receiver, range(len(card.receiver))):
        outputs.append(   # TxOut for each receiver, index + 2 because we have two outs already
            tx_output(value=0, seq=index+2, script=p2pkh_script(addr))
        )

    #  first round of txn making is done by presuming minimal fee
    change_sum = float(inputs['total']) - float(network_params.min_tx_fee) - float(pa_params.P2TH_fee)

    outputs.append(
        tx_output(value=change_sum, seq=len(outputs)+1, script=p2pkh_script(change_address))
        )

    return make_raw_transaction(inputs['utxos'], outputs)
