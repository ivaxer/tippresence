# -*- coding: utf-8 -*-

from random import choice
from string import ascii_letters

from twisted.internet import reactor

def random_str(len):
    return "".join(choice(ascii_letters) for x in xrange(len))

