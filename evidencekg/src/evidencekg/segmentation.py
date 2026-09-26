"""Primary ranges partition canonical text; overlap is context only."""


def split(text, size=6000):
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = text.rfind("\n", start + size // 2, end)
            if cut >= start:
                end = cut + 1
        yield start, end
        start = end


def canonical(sections):
    text, locators = "", []
    for section in sections:
        if text:
            text += "\n\n"
        start = len(text)
        text += section["text"]
        locators.append(
            {**{k: v for k, v in section.items() if k != "text"}, "start": start, "end": len(text)}
        )
    return text, locators
