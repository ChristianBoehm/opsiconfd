#!/usr/bin/env sh

echo "setup mysql"
echo $MYSQL_ROOT_PASSWORD
echo $MYSQL_DATABASE
echo $MYSQL_USER
echo $MYSQL_PASSWORD

service mysql start

echo "set mysql root pw"
sudo mysqladmin -u root --password=$MYSQL_OLD_ROOT_PASSWORD password $MYSQL_ROOT_PASSWORD
mysql -u root --password=$MYSQL_ROOT_PASSWORD -e "DELETE FROM mysql.user WHERE User=''"
echo "remove test db"
mysql -u root --password=$MYSQL_ROOT_PASSWORD -e "DELETE FROM mysql.db WHERE Db='test' OR Db='test\_%'"
echo "FLUSH"
mysql -u root --password=$MYSQL_ROOT_PASSWORD -e "FLUSH PRIVILEGES"

echo "create opsi user"
mysql -u root --password=$MYSQL_ROOT_PASSWORD -e "CREATE USER $MYSQL_USER@localhost IDENTIFIED BY '$MYSQL_PASSWORD';"
mysql -u root --password=$MYSQL_ROOT_PASSWORD -e "GRANT ALL PRIVILEGES ON *.* TO $MYSQL_USER@localhost IDENTIFIED BY '$MYSQL_PASSWORD';"
mysql -u root --password=$MYSQL_ROOT_PASSWORD -e "FLUSH PRIVILEGES"
echo "create opsi db"
mysql -u root --password=$MYSQL_ROOT_PASSWORD -e "CREATE DATABASE $MYSQL_DATABASE;"

echo 'Restore opsi database' 
echo $OPSI_HOSTNAME
zcat /opsi.sql.gz | sed 's/bonifax.uib.local/'$OPSI_HOSTNAME'/g'  | mariadb -h localhost -u opsi --password=opsi opsi

service mysql stop