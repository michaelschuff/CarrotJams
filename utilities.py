from collections import namedtuple

class Queue:
    def __init__(self):
        self.music = namedtuple('music', ('title', 'url', 'thumb'))
        self.current_music = self.music('', '', '')
        self.last_title_enqueued = ''
        self.queue = []

    def set_last_as_current(self):
        index = len(self.queue) - 1
        if index >= 0:
            self.current_music = self.queue[index]

    def enqueue(self, music_title, music_url, music_thumb):
        self.queue.append(self.music(music_title, music_url, music_thumb))
        self.last_title_enqueued = music_title
        if len(self.queue) == 1:
            self.current_music = self.queue[0]

    def dequeue(self):
        if self.queue:
            return self.queue.pop(0)
        return None

    def previous(self):
        if not self.queue or self.current_music not in self.queue:
            return
        index = self.queue.index(self.current_music) - 1
        if index >= 0:
            self.current_music = self.queue[index]

    def next(self):
        if self.queue and self.current_music in self.queue:
            index = self.queue.index(self.current_music) + 1
            if index <= len(self.queue) - 1:
                self.current_music = self.queue[index]
            else:
                # if no more music, clear current
                self.current_music = self.music('', '', '')
        else:
            self.clear_queue()

    def theres_next(self):
        if not self.queue or self.current_music not in self.queue:
            return False
        return self.queue.index(self.current_music) + 1 <= len(self.queue) - 1

    def clear_queue(self):
        self.queue.clear()
        self.current_music = self.music('', '', '')


class Session:
    def __init__(self, guild, channel, id=0):
        self.id = id
        self.guild = guild
        self.channel = channel
        self.q = Queue()
