Visual Loop Machine
###################

Visual Loop Machine plays visual loops stored in the MTD (Multiple Temporal Dimension) video format. The visual loop
changes according to the loudness of the audio playing on the same computer.

.. image:: gui.png


| More details here: https://computervisionblog.wordpress.com/2022/04/30/visual-loop-machine/

Example videos:
 | https://youtu.be/9IMoNuqwvhs
 | https://youtu.be/jDYyhgoLwZ0

Quickstart
==========
Create virtual environment and install v_machine

First download repository.

.. code-block:: console

    $ git clone https://github.com/goolygu/v_machine.git

Switch to Qt6 branch if running on Apple silicon.

.. code-block:: console

    $ git checkout qt6

For Linux users, you may need to install portaudio.

.. code-block:: console

    $ sudo apt-get install libportaudio2


Make virtual environment and install packages.

.. code-block:: console

    $ cd v_machine
    $ make venv

Place mtd videos to play under the mtd_video folder (Samples are provided.) You can download mtd videos
created by me here https://drive.google.com/drive/folders/16wlG6fFPS-srPqVNeYKTvZyl0b4hTfPi?usp=sharing

Activate virtual environment and run the following command to start

.. code-block:: console

    $ source venv/bin/activate
    $ python src/v_machine/v_machine.py

There is a drop down menu where you can select the input source as shown in the image above.

For Mac users, the default input is usually the built-in microphone, if you would like to route the music output to v_machine directly, you will need to install a virtual output/input device:

- For Mac with Intel processor, you can install soundflower https://github.com/mattingalls/Soundflower
- For Mac with Apple Silicon, you can install https://github.com/ExistentialAudio/BlackHole

After installation, you can use the built-in Audio MIDI Setup to create a Multi-Output device and check both the virtual device and the speaker, then set this Multi-Output device as the system audio output, then in v_machine select to use the virtual device as audio input source
