"""从检索工具返回的多个网页中分开读取来源，避免把后一页正文挂到前一页网址。"""
import re

SEPARATOR=re.compile(r'(-{40,}\r?\n)')
HEADER=re.compile(r'(?m)^([^\n]{1,350}?)\s+\((https?://[^\s)]+)\)\s*$')
SOURCE=re.compile(r'Source:\s*(open|find|click)\(')

def pieces(text):
    return SEPARATOR.split(text)

def opened(block):
    header=HEADER.search(block);source=SOURCE.search(block)
    lines=re.findall(r'(?m)^L(\d+(?:@P\d+)?):[ \t]*([^\r\n]*)',block)
    if not header or not source or not lines:return None
    return {'title':header[1],'url':header[2],'kind':source[1],'lines':lines}
