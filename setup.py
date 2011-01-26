from setuptools import setup, find_packages

if __name__ == '__main__':
    setup(
            name='tippresence',
            description='Presence server with SIP/HTTP interfaces written in Twisted Python',
            license='MIT',
            url = 'http://github.com/ivaxer/tippresence',
            keywords = "SIP VoIP",

            author='John Khvatov',
            author_email='ivaxer@imarto.net',

            install_requires = ['tipsip'],

            packages = find_packages(),

            test_suite='tippresence.tests',
            package_data = {
                'tippresence.amqp': ['amqp0-8.xml'],
            }
    )
