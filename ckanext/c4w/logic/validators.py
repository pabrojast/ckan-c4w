# encoding: utf-8
"""Validators for ckanext-c4w.

Vocabulary validators are built by a FACTORY that closes over the vocabulary
name and reads ``constants.VOCABULARIES``, so a vocabulary is never spelled
out twice: adding a term to constants.py is the whole change.
"""


def get_validators():
    return {}
