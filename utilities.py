from collections import namedtuple
import random

class Queue:
    def __init__(self):
        self.music = namedtuple('music', ('title', 'url', 'thumb', 'link'))
        self.queue = []
        self.current_music = None
        self.curr_index = -1
        self.loop = False

    def enqueue(self, title, url, thumb, link):
        self.queue.append(self.music(title, url, thumb, link))
        if self.curr_index == -1:
            self.curr_index = 0
            self.current_music = self.queue[self.curr_index]

    def set_first_as_current(self):
        if len(self.queue) > 0:
            self.curr_index = 0
            self.current_music = self.queue[self.curr_index]

    def next(self):
        if self.curr_index == -1 and len(self.queue) == 0:
            return False
        
        if self.curr_index == len(self.queue) - 1:
            return False

        
        
        if self.loop:
            self.curr_index = (self.curr_index + 1) % len(self.queue)
        else:
            del self.queue[self.curr_index]

        self.current_music = self.queue[self.curr_index]
        return True
    
    def has_next(self):
        if self.curr_index == -1:
            return len(self.queue) > 0
        
        if self.loop:
            return len(self.queue) > 0

        if self.curr_index < len(self.queue)-1:
            return True

        return False

    def clear_queue(self):
        self.queue = []
        self.curr_index = -1
        self.current_music = None
        self.loop = False

    def shuffle(self):
        if self.queue != [] or self.curr_index != -1 or self.current_music != None:
            print("Cannot do ordinary shuffle while playing music")
        random.shuffle(self.queue)

    def shuffle_while_playing(self):
        if self.queue == [] and self.curr_index == -1 and self.current_music == None:
            shuffle()
            return
        
        del queue[self.curr_index]
        random.shuffle(queue)
        self.queue.insert(self.current_music)
        self.curr_index = 0


class Session:
    def __init__(self, guild, channel, id=0):
        self.id = id
        self.guild = guild
        self.channel = channel
        self.q = Queue()
        self.is_paused = False
