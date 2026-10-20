class Base:
    def render(self, rows):
        return self.clean(rows)

    def clean(self, rows):
        return rows


class Formatter(Base):
    pass
