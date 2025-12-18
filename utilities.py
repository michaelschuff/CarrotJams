from collections import namedtuple


class Queue:
    def __init__(self):
        self.music = namedtuple('music', ('title', 'url', 'thumb'))
        self.queue = []
        self.current_music = None
        self.curr_index = -1
    def enqueue(self, title, url, thumb):
        self.queue.append(self.music(title, url, thumb))
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

        
        self.curr_index += 1
        self.current_music = self.queue[self.curr_index]
        return True
    
    def has_next(self):
        if self.curr_index == -1:
            return len(self.queue) > 0
    
        if self.curr_index < len(self.queue)-1:
            return True

        return False

    def clear_queue(self):
        self.queue = []
        self.curr_index = -1
        self.current_music = None


class Session:
    def __init__(self, guild, channel, id=0):
        self.id = id
        self.guild = guild
        self.channel = channel
        self.q = Queue()
