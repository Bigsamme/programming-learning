#learning what is happening under the hood in python 

""" 
processor fetches from memeroy and does the task
the processor just does that over and over
machine code is what is stored in the memery which is just ones 
and zeros 
"""

"""
the .py or .cpp or anycode file is just a text file stored on the hard drive 
the compiler takes the sourse code and translated it to machine code
for the specfic platform and processer
"""

"""
that is true for cpp, c, and go python is diffrent 
because the python interpreter is binary and has two things the compiler and 
the python VM.

So python takes the file and uses the compiler to translates it to byte code
which the python VM can run other languates like java also do this


Also Called Cpython
"""



print("hello world")
"""
to get just the byte code you can run python -m py_complie file_name.py
it put the file in __pycache__ we can get to that by running ls __pycache__
to check the contents you can run cat __pycache__/file_name_you_got_from python -m py_complie file_name.py
"""

"""
to get a human readable version you can run python -m dis file_name.py
which gives you s list of instruction of what it does
"""