# attempt to enable gnu11 up to gnu2y
# we skip gnu2x here due to its inconsistency
CFLAGS_ksu.o += $(call cc-option, -std=gnu11)
CFLAGS_ksu.o += $(call cc-option, -std=gnu17)
CFLAGS_ksu.o += $(call cc-option, -std=gnu23)
CFLAGS_ksu.o += $(call cc-option, -std=gnu2y)

#ifeq ($(shell $(CC) -v 2>&1 | grep -q 'clang'; echo $$?),0)
# $(info -- KernelSU/CC: clang!)
#endif
