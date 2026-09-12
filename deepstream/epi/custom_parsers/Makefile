CC:= g++
CFLAGS:= -Wall -std=c++14 -shared -fPIC
INCS:= -I/opt/nvidia/deepstream/deepstream/sources/includes -I/usr/local/cuda/include
LIBS:= -shared
all: libnvdsparsebbox_rfdetr.so
libnvdsparsebbox_rfdetr.so: nvdsparsebbox_rfdetr.cpp
	$(CC) $(CFLAGS) $(INCS) -o $@ $< $(LIBS)
clean:
	rm -f *.so
