"""Checkin new git changesets to Clearcase"""

from common import *
from clearcase import cc
from status import Modify, Add, Delete, Rename, SymLink
import filecmp
from os import listdir
from os.path import isdir
import cache, reset

IGNORE_CONFLICTS=False
LOG_FORMAT = '%H%x01%B'
CC_LABEL = ''

ARGS = {
    'force': 'ignore conflicts and check-in anyway',
    'no_deliver': 'do not deliver in UCM mode',
    'initial': 'checkin everything from the beginning',
    'all': 'checkin all parents, not just the first',
    'cclabel': 'optionally specify an existing Clearcase label type to apply to each element checked in',
    'preview': 'stage all changes to Clearcase and leave files checked out. If changes are checked into Clearcase manually - run "git tag -f master_ci" afterwards',
}

def main(force=False, no_deliver=False, initial=False, all=False, cclabel='', preview=False):
    validateCC()
    global IGNORE_CONFLICTS
    global CC_LABEL
    if cclabel:
        CC_LABEL=cclabel
    if force:
        IGNORE_CONFLICTS=True
    cc_exec(['update', '.'], errors=False)
    log = ['log', '-z', '--reverse', '--pretty=format:'+ LOG_FORMAT ]
    if not all:
        log.append('--first-parent')
    if not initial:
        log.append(CI_TAG + '..')
    log = git_exec(log)
    if not log:
        return
    cc.rebase()
    transaction = ITransaction('') if initial else Transaction('')
    for line in log.split('\x00'):
        id, comment = line.split('\x01')
        comment = comment.strip()
        statuses = getStatuses(id, initial)
        if not preview:
            transaction = ITransaction(comment) if initial else Transaction(comment)
        checkout(statuses, transaction)
        if not preview :
            transaction.commit(comment);
            tag(CI_TAG, id)
    if not no_deliver:
        cc.commit()
    if initial:
        git_exec(['commit', '--allow-empty', '-m', 'Empty commit'])
        reset.main('HEAD')

def getStatuses(id, initial):
    cmd = ['diff','--name-status', '-M', '-z', '--ignore-submodules', '%s^..%s' % (id, id)]
    if initial:
        cmd = cmd[:-1]
        cmd[0] = 'show'
        cmd.extend(['--pretty=format:', id])
    status = git_exec(cmd)
    status = status.strip()
    status = status.strip("\x00")
    types = {'M':Modify, 'R':Rename, 'D':Delete, 'A':Add, 'C':Add, 'S':SymLink}
    list = []
    split = status.split('\x00')
    while len(split) > 1:
        char = split.pop(0)[0] # first char
        args = [split.pop(0)]
        # check if file is really a symlink
        cmd = ['ls-tree', '-z', id, '--', args[0]]
        if git_exec(cmd).split(' ')[0] == '120000':
            char = 'S'
            args.append(id)
        if char == 'R':
            args.append(split.pop(0))
        elif char == 'C':
            args = [split.pop(0)]
        if args[0] == cache.FILE:
            continue
        type = types[char](args)
        type.id = id
        list.append(type)
    return list

def checkout(stats, transaction):
    """Poor mans two-phase commit"""
    for stat in stats:
        try:
            stat.stage(transaction)
        except:
            transaction.rollback()
            raise

    for stat in stats:
         stat.commit(transaction)

class ITransaction(object):
    def __init__(self, comment):
        self.checkedout = []
        self.cc_label = CC_LABEL
        cc.mkact(comment)
    def add(self, file):
        self.checkedout.append(file)
    def co(self, file):
        if file not in self.checkedout:
            cc_exec(['co', '-reserved', '-nc', file])
            if CC_LABEL:
                cc_exec(['mklabel', '-replace', '-nc', CC_LABEL, file])
            self.add(file)
    def stageDir(self, file):
        file = file if file else '.'
        self.co(file)
    def stage(self, file):
        self.co(file)
    def rollback(self):
        for file in self.checkedout:
            cc_exec(['unco', '-rm', file])
        cc.rmactivity()
    def commit(self, comment):
        for file in self.checkedout:
            cc_exec(['ci', '-identical', '-c', comment, file])
        self.checkedout = []

class Transaction(ITransaction):
    def __init__(self, comment):
        super(Transaction, self).__init__(comment)
        self.base = git_exec(['merge-base', CI_TAG, 'HEAD']).strip()
    def stage(self, file):
        if file not in self.checkedout:
            ccid = git_exec(['hash-object', join(CC_DIR, file)])[0:-1]
            gitid = getBlob(self.base, file)
            if ccid != gitid:
                if not IGNORE_CONFLICTS:
                    raise Exception('File has been modified: %s. Try rebasing.' % file)
                else:
                    print ('WARNING: Detected possible confilct with',file,'...ignoring...')
            super(Transaction, self).stage(file)
