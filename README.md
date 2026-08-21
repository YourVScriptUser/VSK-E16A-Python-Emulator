**VSK-E16A Emulator**


**An Overview**

The VSK-E16A is a simple 16-bit emulator written in python.

Includes:

  stack-based PMIO (see PMIO STACK)
  
  64K Of word-addressed memory (see ADDRESSING)
  
  256-word IVT (see Interrupts)
  
  hardware interrupts (see Interrupts)

  speed (iF 0): ~800khz max
  
  speed (iF 1): ~200khz max
  
  max speed is different based on HWI status as `check_HWI_status()` is taxing on performance
  
  64K-word addressed 'disk' (see disk)

  fully compatible with PyPy3
  
  .vsix vscode extension for xasme16a syntax highlighting and hover explanations inside
  
  much more in-depth (VIBE CODED!!!) .HTML manual inside

  included full OS (ahoxOS) and official BIOS source

  included boot.bat file (requires wezterm)



The assembler for xasme16a lives inside the emulator (see pre-run shell)

**Pre-Run Shell**
The .py source includes a live shell upon pressing `ctrl` on startup. You can list commands using the `help` command

**PMIO STACK**
The PMIO in this emulator is stack-based, each device shares a 255-word LIFO stack for communicating with devices

**ADDRESSING**
Each address in disk *and* memory can hold a 16-bit unsigned variable. There is no byte-addressing or bit shifting.

**Interrupts**
Hardware interrupts go through `check_HWI_status` which gets called every tick when iF is 1 (HWIs enabled) - if a interrupt is ready it will go through the `DoHardwareInterrupt` class.

**NOTES**
Please note that:
  1. This is not 100% bug-free. Not even close. Expect random bugs from time to time, but no known bugs cause the CPU to behave in a way that contradicts its intended design.
  2. Some parts of the emulator are vibe coded (using claude). List of things made by AI here:
       - The debugger GUI
       - .HTML manual
       - Disassembler
  3. This was never meant to be a publicly released project, therefore the paths are fixed to 'C:\python\VSK-E16A Workspace\', but booting .img and imaging .xsm files outside of this dir are still supported.
     


  

  
