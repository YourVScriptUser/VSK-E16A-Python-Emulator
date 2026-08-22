#osname "Testing"
// #osname is a valid directive for the vskcc compiler
// it specifies the OS string for the boot table

char something[30] = "hi";

char letter = 'A';
int  number = 99;



int main(void) {
    printf("Letter is: %c, Number is: %d, Number in hex 0x%x", letter, number, number);
    vskasm(
        "mov r0, r1",
        "mov r1, r0"
    );
    return 0;
}






